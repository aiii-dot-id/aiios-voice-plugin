    // N=1 needs one output column, not the generic tile's eight columns.
    // Four lanes cooperate on each row. Reduction uses shared memory only;
    // it does not assume a physical subgroup size or relax F32 precision.
    if (p.N == 1) {
        uint lane = tid % 4;
        uint row = row_block + tid / 4;
        float sum = 0.0;
        if (row < p.M) {
            for (uint k = start + lane; k < end; k += 4) {
                sum = fma(a[ba*p.batch_stride_a + row*p.stride_a + k],
                          b[batch*p.batch_stride_b + k], sum);
            }
        }
        sa[tid] = sum;
        barrier();
        if (lane == 0 && row < p.M) {
            uint offset = (batch + split*p.num_batches)*p.batch_stride_d;
            d[offset + row] = (sa[tid] + sa[tid+1]) + (sa[tid+2] + sa[tid+3]);
        }
        return;
    }
