"""Narrow, checked GPU export transformations, never an inference fallback."""
import torch


def prove_x32_domain(module, component):
    """Prove every live x64 value representable before converter downcasting.

    The only external integer values are a validated feature length or token ID.
    Unknown integer computations and live float64 values refuse export. The
    device adapter must enforce the same input domains before inference.
    """
    domains={'encoder':('valid_frames',(1,),0,2000),'predictor':('token',(1,1),0,8192)}
    if component not in (*domains,'joint'):raise ValueError('Unknown component')
    proof={}
    def bounds(node):
        if type(node) is int:return node,node
        if not isinstance(node,torch.fx.Node):raise ValueError('Unknown x64 operand')
        if node.name in proof:return tuple(proof[node.name]['range'])
        target=node.target;value=node.meta.get('val')
        if node.op=='placeholder':
            if component not in domains:raise ValueError('Unexpected integer input')
            name,shape,low,high=domains[component]
            if str(target)!=name or tuple(value.shape)!=shape:raise ValueError('Unbound integer input')
        elif node.op=='get_attr':
            actual=module
            for part in str(target).split('.'):actual=getattr(actual,part)
            if actual.dtype!=torch.int64 or not actual.numel():raise ValueError('Unproved integer constant')
            low,high=int(actual.min()),int(actual.max())
        elif target in (torch.ops.aten.arange.default,torch.ops.aten.arange.start_step):
            if target==torch.ops.aten.arange.default:start,stop,step=0,node.args[0],1
            else:start,stop,step=node.args
            if any(type(v) is not int for v in (start,stop,step)) or step==0:raise ValueError('Dynamic integer range')
            r=range(start,stop,step)
            if not r or len(r)>2000:raise ValueError('Integer range extent differs')
            low,high=min(r[0],r[-1]),max(r[0],r[-1])
        elif target==torch.ops.aten.sum.dim_IntList:
            v=node.args[0].meta.get('val')
            if v is None or v.dtype!=torch.bool or tuple(v.shape)!=(1,2000) or list(node.args[1])!=[-1]:
                raise ValueError('Unproved count')
            low,high=0,2000
        elif target in (torch.ops.aten.unsqueeze.default,torch.ops.aten.squeeze.dim,torch.ops.aten.view.default,
                        torch.ops.aten.reshape.default,torch.ops.aten.clone.default):
            low,high=bounds(node.args[0])
        elif target in (torch.ops.aten.add.Tensor,torch.ops.aten.sub.Tensor):
            if len(node.args)!=2 or node.kwargs.get('alpha',1)!=1:raise ValueError('Unproved arithmetic')
            a,b=bounds(node.args[0]);c,d=bounds(node.args[1])
            low,high=(a+c,b+d) if target==torch.ops.aten.add.Tensor else (a-d,b-c)
        elif target==torch.ops.aten.floor_divide.default:
            divisor=node.args[1]
            if type(divisor) is not int or divisor<=0:raise ValueError('Unproved division')
            a,b=bounds(node.args[0]);low,high=a//divisor,b//divisor
        else:raise ValueError('Unproved live x64 operation: '+str(target))
        if not -(2**31)<=low<=high<2**31:raise ValueError('Integer narrowing would overflow')
        proof[node.name]={'target':str(target),'range':[low,high]}
        return low,high
    for node in module.graph.nodes:
        if not node.users:continue
        value=node.meta.get('val');dtype=getattr(value,'dtype',None)
        if dtype==torch.float64:raise ValueError('Live float64 cannot be silently downcast')
        if dtype==torch.int64:bounds(node)
    return proof


def repeat_attention_mask(module):
    """Replace the sole bool [1,1,250]->[1,250,250] expand with exact TILE.

    No data-dependent rewrite or changed mask semantics. Other expands are
    unchanged, and any different boolean expand refuses this transformation.
    """
    found=[]
    for node in module.graph.nodes:
        if node.target!=torch.ops.aten.expand.default:continue
        value=node.meta.get('val')
        if getattr(value,'dtype',None)!=torch.bool:continue
        source=node.args[0].meta.get('val')
        if source is None or tuple(source.shape)!=(1,1,250) or tuple(value.shape)!=(1,250,250) or tuple(node.args[1])!=(-1,250,-1):
            raise ValueError('Boolean mask expansion differs')
        found.append(node)
    if len(found)!=1:raise ValueError('Expected exactly one attention-mask expansion')
    node=found[0];node.target=torch.ops.aten.repeat.default;node.args=(node.args[0],[1,250,1]);node.kwargs={}
    module.graph.lint();module.recompile()
    return {'node':node.name,'from_shape':[1,1,250],'to_shape':[1,250,250],'repeats':[1,250,1]}


def explicit_safe_attention(q,k,v,bias,*,scale):
    """Preserve SDPA zero rows without softmax(-inf, ..., -inf).

    Nonempty rows have a maximum weight of exactly one; clamp_min(1) changes
    only empty rows. Positive infinity and NaN are not sanitized.
    """
    scores=torch.matmul(q,k.transpose(-2,-1))*scale+bias
    admitted=scores!=float('-inf')
    finite=torch.where(admitted,scores,torch.finfo(scores.dtype).min)
    weights=torch.exp(finite-finite.amax(dim=-1,keepdim=True))*admitted
    probabilities=weights/weights.sum(dim=-1,keepdim=True).clamp_min(1.0)
    return torch.matmul(probabilities,v)


def lower_safe_attention(module):
    """Replace exactly 24 pinned FP32, noncausal, dropout-free SDPA calls."""
    found=[n for n in module.graph.nodes if n.target==torch.ops.aten.scaled_dot_product_attention.default]
    if len(found)!=24:raise ValueError('Expected exactly 24 encoder attention operations')
    for node in found:
        if len(node.args)!=4 or node.kwargs!={'scale':128**-0.5}:
            raise ValueError('Attention scale/dropout/causal contract differs')
        for i,arg in enumerate(node.args):
            value=arg.meta.get('val') if isinstance(arg,torch.fx.Node) else None
            shape=(1,8,250,250) if i==3 else (1,8,250,128)
            if getattr(value,'dtype',None)!=torch.float32 or tuple(value.shape)!=shape:
                raise ValueError('Attention tensor contract differs')
    for node in found:node.target=explicit_safe_attention
    module.graph.lint();module.recompile()
    return {'nodes':[n.name for n in found],'scale':128**-0.5,'masked_row_semantics':'zero-before-normalization'}


def gpu_inputs(inputs,component):
    """Public GPU graph uses int32; reject rather than wrap out-of-domain IDs."""
    import numpy as np
    result=list(inputs)
    if component not in ('encoder','predictor','joint'):raise ValueError('Unknown component')
    if component=='joint':return tuple(result)
    index=1 if component=='encoder' else 0
    value=np.asarray(result[index]);shape=(1,) if component=='encoder' else (1,1)
    maximum=2000 if component=='encoder' else 8192
    if value.shape!=shape or value.dtype not in (np.dtype('int64'),np.dtype('int32')) or (value<0).any() or (value>maximum).any():
        raise ValueError('Out-of-domain integer input')
    result[index]=value.astype(np.int32)
    return tuple(result)
