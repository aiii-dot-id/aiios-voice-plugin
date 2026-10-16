#import <Foundation/Foundation.h>
#import <CoreML/CoreML.h>
#import <CommonCrypto/CommonDigest.h>
#import <TargetConditionals.h>
#if TARGET_OS_IPHONE
#import <UIKit/UIKit.h>
#endif
#include <cmath>
#include <cstdio>
#include <cstring>
#include <vector>
#include <stdexcept>
#include "uid_frontend.h"

// A physical-device model proof. No microphone, enrollment mutation, SDK
// default change or claim that a compute plan is a hardware execution trace.
static void Need(bool ok, const char *why) {
  if (!ok) throw std::runtime_error(why);
}

static NSString *Hash(NSData *data) {
  unsigned char bytes[CC_SHA256_DIGEST_LENGTH];
  CC_SHA256(data.bytes, (CC_LONG)data.length, bytes);
  NSMutableString *out = [NSMutableString stringWithCapacity:64];
  for (int i = 0; i < CC_SHA256_DIGEST_LENGTH; ++i) [out appendFormat:@"%02x", bytes[i]];
  return out;
}

static id ReadJSON(NSURL *url) {
  NSError *error = nil;
  NSData *data = [NSData dataWithContentsOfURL:url options:0 error:&error];
  Need(data != nil, "resource missing");
  id result = [NSJSONSerialization JSONObjectWithData:data options:0 error:&error];
  Need(result != nil, "invalid JSON");
  return result;
}

static void Emit(NSString *marker, id result) {
  NSError *error = nil;
  NSData *data = [NSJSONSerialization dataWithJSONObject:result options:0 error:&error];
  Need(data != nil, "non-finite or invalid report");
  printf("%s %s\n", marker.UTF8String, [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding].UTF8String);
  fflush(stdout);
}

static void WalkPlan(MLComputePlan *plan, MLModelStructureProgramBlock *block, NSMutableArray *rows) {
  for (MLModelStructureProgramOperation *op in block.operations) {
    MLComputePlanDeviceUsage *usage = [plan computeDeviceUsageForMLProgramOperation:op];
    NSString *name = op.operatorName;
    NSString *base = [[name componentsSeparatedByString:@"."] lastObject];
    BOOL heavy = [@[@"conv", @"linear", @"matmul"] containsObject:base];
    BOOL gpu = usage && [usage.preferredComputeDevice conformsToProtocol:@protocol(MLComputeDeviceProtocol)] &&
               [usage.preferredComputeDevice isKindOfClass:[MLGPUComputeDevice class]];
    [rows addObject:@{@"operator": name, @"heavy": @(heavy), @"preferred_gpu": @(gpu),
                     @"preferred": usage ? NSStringFromClass([usage.preferredComputeDevice class]) : @"none"}];
    for (MLModelStructureProgramBlock *nested in op.blocks) WalkPlan(plan, nested, rows);
  }
}

static int Run() {
  @autoreleasepool {
    try {
      NSBundle *bundle = [NSBundle mainBundle];
      NSURL *panelURL = [bundle URLForResource:@"panel" withExtension:@"json"];
      NSDictionary *panel = ReadJSON(panelURL);
      NSArray *samples = panel[@"samples"];
      Need(samples.count == 161, "whole frozen panel required");
      BOOL nativePCM = [panel[@"input_mode"] isEqualToString:@"native_pcm16"];
      Need(nativePCM || [panel[@"input_mode"] isEqualToString:@"prepared_features"], "unknown input mode");
      NSURL *modelURL = [bundle URLForResource:@"wespeaker" withExtension:@"mlmodelc"];
      Need(modelURL != nil, "compiled model missing");
      NSDictionary *binding = ReadJSON([bundle URLForResource:@"compiled-model-binding" withExtension:@"json"]);
      for (NSString *path in binding[@"files"]) {
        Need(![path hasPrefix:@"/"] && ![[path pathComponents] containsObject:@".."], "unsafe resource path");
        NSData *bytes = [NSData dataWithContentsOfURL:[modelURL URLByAppendingPathComponent:path]];
        Need(bytes != nil && [Hash(bytes) isEqualToString:binding[@"files"][path]], "compiled model hash differs");
      }
      double begun = CFAbsoluteTimeGetCurrent();
      MLModelConfiguration *configuration = [MLModelConfiguration new];
      configuration.computeUnits = MLComputeUnitsCPUAndGPU;
      NSError *error = nil;
      MLModel *model = [MLModel modelWithContentsOfURL:modelURL configuration:configuration error:&error];
      if (!model) throw std::runtime_error(error.localizedDescription.UTF8String);
      double setup = CFAbsoluteTimeGetCurrent() - begun;
      __block MLComputePlan *plan = nil;
      __block NSError *planError = nil;
      dispatch_semaphore_t ready = dispatch_semaphore_create(0);
      [MLComputePlan loadContentsOfURL:modelURL configuration:configuration completionHandler:^(MLComputePlan *p, NSError *e) {
        plan = p; planError = e; dispatch_semaphore_signal(ready);
      }];
      Need(dispatch_semaphore_wait(ready, dispatch_time(DISPATCH_TIME_NOW, 30 * NSEC_PER_SEC)) == 0, "compute-plan timeout");
      if (!plan) throw std::runtime_error(planError.localizedDescription.UTF8String);
      Need(plan.modelStructure.program != nil, "MLProgram required");
      NSMutableArray *planRows = [NSMutableArray array];
      for (MLModelStructureProgramFunction *fn in plan.modelStructure.program.functions.allValues)
        WalkPlan(plan, fn.block, planRows);
      NSUInteger heavy = 0, heavyGPU = 0;
      for (NSDictionary *row in planRows) if ([row[@"heavy"] boolValue]) {
        ++heavy; if ([row[@"preferred_gpu"] boolValue]) ++heavyGPU;
      }
      Emit(@"AII_UID_PLAN", planRows);
      NSMutableSet *seen = [NSMutableSet set];
      double maximumError = 0, minimumCosine = 1;
      NSMutableArray *outputs = [NSMutableArray array];
      for (NSDictionary *row in samples) {
        @autoreleasepool {
          Need(CFAbsoluteTimeGetCurrent() - begun < 300, "device proof exceeded five minutes");
          NSString *identifier = row[@"id"];
          Need(![seen containsObject:identifier], "duplicate panel row");
          [seen addObject:identifier];
          NSInteger frames = [row[@"frames"] integerValue];
          Need(frames >= 198 && frames <= 3000, "bounded whole utterance required");
          NSString *file = row[@"feature_file"];
          Need([[file lastPathComponent] isEqualToString:file], "unsafe feature path");
          NSData *data = [NSData dataWithContentsOfURL:[bundle.resourceURL URLByAppendingPathComponent:file]];
          Need(data.length == (NSUInteger)(frames * 80 * sizeof(float)), "feature size differs");
          Need([Hash(data) isEqualToString:row[@"feature_sha256"]], "feature hash differs");
          std::vector<float> nativeFeatures;
          double frontendSeconds = 0, frontendError = 0;
          NSString *pcmHash = @"";
          if (nativePCM) {
            NSString *pcmFile = row[@"pcm_file"];
            Need([[pcmFile lastPathComponent] isEqualToString:pcmFile], "unsafe PCM path");
            NSData *pcm = [NSData dataWithContentsOfURL:[bundle.resourceURL URLByAppendingPathComponent:pcmFile]];
            Need(pcm != nil && pcm.length == [row[@"pcm_samples"] unsignedIntegerValue] * 2, "PCM size differs");
            pcmHash = Hash(pcm);
            Need([pcmHash isEqualToString:row[@"pcm_sha256"]], "PCM hash differs");
            nativeFeatures.resize(frames * 80);
            size_t actualFrames = 0;
            double frontendStart = CFAbsoluteTimeGetCurrent();
            int result = aiii_uid_fbank((const uint8_t *)pcm.bytes, pcm.length,
                [row[@"sample_rate"] intValue], nativeFeatures.data(), nativeFeatures.size(),
                &actualFrames, nullptr, nullptr);
            frontendSeconds = CFAbsoluteTimeGetCurrent() - frontendStart;
            Need(result == 0 && actualFrames == (size_t)frames, "native frontend refused or changed frame count");
            const float *reference = (const float *)data.bytes;
            for (size_t i=0; i<nativeFeatures.size(); ++i)
              frontendError = std::max(frontendError, std::abs(double(nativeFeatures[i]) - reference[i]));
            // Log-filterbank parity follows upstream's 1e-3 compatibility
            // tolerance; the end-to-end embedding gate is unchanged (1e-4).
            Need(frontendError < 0.001, "native frontend parity failed");
          }
          MLMultiArray *input = [[MLMultiArray alloc] initWithShape:@[@1, @(frames), @80] dataType:MLMultiArrayDataTypeFloat32 error:&error];
          Need(input != nil, "input allocation failed");
          // Reference features are an oracle only in native mode: the MODEL
          // consumes the frontend's actual PCM-derived output, never the oracle.
          const float *features = nativePCM ? nativeFeatures.data() : (const float *)data.bytes;
          for (NSInteger t=0; t<frames; ++t) for (NSInteger f=0; f<80; ++f) {
            float v = features[t*80+f];
            Need(std::isfinite(v), "non-finite feature");
            NSInteger offset = t * input.strides[1].integerValue + f * input.strides[2].integerValue;
            ((float *)input.dataPointer)[offset] = v;
          }
          MLDictionaryFeatureProvider *inputs = [[MLDictionaryFeatureProvider alloc] initWithDictionary:@{@"feats": input} error:&error];
          double start = CFAbsoluteTimeGetCurrent();
          id<MLFeatureProvider> prediction = [model predictionFromFeatures:inputs error:&error];
          double elapsed = CFAbsoluteTimeGetCurrent() - start;
          if (!prediction) throw std::runtime_error(error.localizedDescription.UTF8String);
          MLMultiArray *embedding = [prediction featureValueForName:@"embs"].multiArrayValue;
          Need(embedding.count == 256, "embedding dimension differs");
          std::vector<double> values(256);
          double norm = 0;
          for (NSInteger i=0; i<256; ++i) {
            values[i] = embedding[i].doubleValue;
            Need(std::isfinite(values[i]), "non-finite embedding");
            norm += values[i] * values[i];
          }
          Need(norm > 1e-24, "zero embedding");
          norm = std::sqrt(norm);
          NSArray *expected = row[@"expected_embedding"];
          Need(expected.count == 256, "reference dimension differs");
          double cosine = 0, deviation = 0;
          NSMutableArray *vector = [NSMutableArray arrayWithCapacity:256];
          for (NSInteger i=0; i<256; ++i) {
            values[i] /= norm;
            double target = [expected[i] doubleValue];
            Need(std::isfinite(target), "non-finite reference");
            cosine += values[i] * target;
            deviation = std::max(deviation, std::abs(values[i]-target));
            [vector addObject:@(values[i])];
          }
          maximumError = std::max(maximumError, deviation);
          minimumCosine = std::min(minimumCosine, cosine);
          [outputs addObject:@{@"id": identifier, @"frames": @(frames), @"model_seconds": @(elapsed),
                               @"cosine": @(cosine), @"max_absolute_error": @(deviation),
                               @"input_mode": panel[@"input_mode"], @"pcm_sha256": pcmHash,
                               @"frontend_seconds": @(frontendSeconds), @"frontend_max_absolute_error": @(frontendError),
                               @"feature_sha256": Hash(data), @"embedding": vector}];
        }
      }
      BOOL passed = maximumError <= 0.0001 && minimumCosine >= 0.9999 && heavy == 39 && heavyGPU == heavy;
      Emit(@"AII_UID_RESULT", @{@"passed": @(passed), @"qualified": @NO, @"gpu_execution_trace": @NO,
           @"scope": @"physical iPhone recorded-input full-context UID and compiled plan; no live microphone or conversation",
           @"input_mode": panel[@"input_mode"],
           @"panel_sha256": Hash([NSData dataWithContentsOfURL:panelURL]),
           @"model_binding_sha256": Hash([NSData dataWithContentsOfURL:[bundle URLForResource:@"compiled-model-binding" withExtension:@"json"]]),
           @"source_model_sha256": panel[@"source_model_sha256"],
           @"setup_seconds": @(setup), @"seconds": @(CFAbsoluteTimeGetCurrent()-begun),
           @"heavy_operations": @(heavy), @"heavy_preferred_gpu": @(heavyGPU),
           @"maximum_absolute_error": @(maximumError), @"minimum_cosine": @(minimumCosine), @"records": outputs});
      return passed ? 0 : 2;
    } catch (const std::exception &error) {
      Emit(@"AII_UID_FAILURE", @{@"error": [NSString stringWithUTF8String:error.what()]});
      return 1;
    }
  }
}

@interface VoiceUIDAppDelegate : UIResponder <UIApplicationDelegate>
@property(nonatomic, strong) UIWindow *window;
@end
@implementation VoiceUIDAppDelegate
- (BOOL)application:(UIApplication *)application didFinishLaunchingWithOptions:(NSDictionary *)options {
  self.window = [[UIWindow alloc] initWithFrame:UIScreen.mainScreen.bounds];
  UIViewController *view = [UIViewController new];
  view.view.backgroundColor = UIColor.blackColor;
  self.window.rootViewController = view;
  [self.window makeKeyAndVisible];
  dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{ exit(Run()); });
  return YES;
}
@end
int main(int argc, char **argv) {
  @autoreleasepool { return UIApplicationMain(argc, argv, nil, NSStringFromClass([VoiceUIDAppDelegate class])); }
}
