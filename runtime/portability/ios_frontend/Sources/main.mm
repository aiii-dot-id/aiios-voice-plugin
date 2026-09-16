#import <UIKit/UIKit.h>

#include "aiii_voice_frontend.h"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <limits>
#include <stdexcept>
#include <string>
#include <sys/sysctl.h>
#include <utility>
#include <vector>

namespace {

struct FrontendHandle {
  vf_frontend* value = nullptr;
  ~FrontendHandle() { vf_frontend_destroy(value); }
};

struct RunResult {
  bool passed;
  std::string json;
};

NSString* Resource(NSString* name) {
  NSString* path = [[NSBundle mainBundle] pathForResource:name ofType:nil];
  if (path == nil) {
    throw std::runtime_error("missing bundled resource");
  }
  return path;
}

std::vector<uint8_t> ReadBytes(NSString* name) {
  NSData* data = [NSData dataWithContentsOfFile:Resource(name)];
  if (data == nil) {
    throw std::runtime_error("cannot read bundled resource");
  }
  std::vector<uint8_t> bytes(data.length);
  if (data.length != 0) {
    std::memcpy(bytes.data(), data.bytes, data.length);
  }
  return bytes;
}

NSString* RequiredString(NSDictionary* value, NSString* key) {
  id raw = value[key];
  if (![raw isKindOfClass:NSString.class] || [raw length] == 0) {
    throw std::runtime_error("case field must be a non-empty string");
  }
  return raw;
}

NSUInteger RequiredUnsigned(NSDictionary* value, NSString* key) {
  id raw = value[key];
  if (![raw isKindOfClass:NSNumber.class] || [raw doubleValue] < 0.0 ||
      [raw doubleValue] != std::floor([raw doubleValue])) {
    throw std::runtime_error("case field must be an unsigned integer");
  }
  return [raw unsignedIntegerValue];
}

double RequiredPositiveDouble(NSDictionary* value, NSString* key) {
  id raw = value[key];
  if (![raw isKindOfClass:NSNumber.class] || !std::isfinite([raw doubleValue]) ||
      [raw doubleValue] <= 0.0) {
    throw std::runtime_error("case field must be a positive finite number");
  }
  return [raw doubleValue];
}

void RequireOK(const char* operation, vf_status status) {
  if (status != VF_OK) {
    throw std::runtime_error(std::string(operation) + " failed: " +
                             vf_status_message(status));
  }
}

std::vector<float> ReadExpected(NSString* name) {
  const auto bytes = ReadBytes(name);
  if (bytes.size() % sizeof(float) != 0) {
    throw std::runtime_error("expected feature resource is not float32 aligned");
  }
  std::vector<float> values(bytes.size() / sizeof(float));
  if (!bytes.empty()) {
    std::memcpy(values.data(), bytes.data(), bytes.size());
  }
  return values;
}

NSDictionary* ExecuteCase(NSDictionary* value) {
  NSString* identifier = RequiredString(value, @"id");
  const auto manifest = ReadBytes(RequiredString(value, @"manifest"));
  const auto pcm = ReadBytes(RequiredString(value, @"pcm"));
  const auto expected = ReadExpected(RequiredString(value, @"expected"));
  const NSUInteger expected_frames = RequiredUnsigned(value, @"expected_frames");
  const NSUInteger expected_bins = RequiredUnsigned(value, @"feature_bins");
  const NSUInteger expected_samples = RequiredUnsigned(value, @"input_samples");
  const double abs_tolerance = RequiredPositiveDouble(value, @"abs_tolerance");
  const double rel_tolerance = RequiredPositiveDouble(value, @"rel_tolerance");
  if (pcm.size() % 2u != 0u || pcm.size() / 2u != expected_samples) {
    throw std::runtime_error("PCM size differs from case metadata");
  }
  if (expected_bins == 0u ||
      expected_frames > std::numeric_limits<size_t>::max() / expected_bins ||
      expected.size() != expected_frames * expected_bins) {
    throw std::runtime_error("expected feature shape differs from case metadata");
  }

  FrontendHandle frontend;
  RequireOK("create", vf_frontend_create(manifest.data(), manifest.size(),
                                          &frontend.value));
  const size_t bins = vf_frontend_feature_bins(frontend.value);
  if (bins != expected_bins) {
    throw std::runtime_error("frontend feature bins differ from case metadata");
  }
  const size_t push_capacity =
      vf_frontend_push_frame_capacity(frontend.value, expected_samples);
  if (push_capacity == SIZE_MAX ||
      (bins != 0u && push_capacity > SIZE_MAX / bins)) {
    throw std::runtime_error("push capacity overflow");
  }
  std::vector<float> push(push_capacity * bins);
  size_t push_frames = 0u;
  RequireOK("push", vf_frontend_push(frontend.value, pcm.data(), expected_samples,
                                      push.empty() ? nullptr : push.data(),
                                      push_capacity, &push_frames));
  if (push_frames != push_capacity) {
    throw std::runtime_error("push emitted a non-exact capacity");
  }

  const size_t flush_capacity = vf_frontend_flush_frame_capacity(frontend.value);
  if (flush_capacity == SIZE_MAX ||
      (bins != 0u && flush_capacity > SIZE_MAX / bins)) {
    throw std::runtime_error("flush capacity overflow");
  }
  std::vector<float> flush(flush_capacity * bins);
  size_t flush_frames = 0u;
  RequireOK("flush", vf_frontend_flush(frontend.value,
                                        flush.empty() ? nullptr : flush.data(),
                                        flush_capacity, &flush_frames));
  if (flush_frames != flush_capacity ||
      push_frames > SIZE_MAX - flush_frames ||
      push_frames + flush_frames != expected_frames) {
    throw std::runtime_error("frontend frame count differs from case metadata");
  }

  push.insert(push.end(), flush.begin(), flush.end());
  if (push.size() != expected.size()) {
    throw std::runtime_error("frontend output shape differs from expected features");
  }
  double maximum = 0.0;
  bool passed = true;
  for (size_t index = 0; index < push.size(); ++index) {
    if (!std::isfinite(push[index]) || !std::isfinite(expected[index])) {
      throw std::runtime_error("frontend output contains a nonfinite value");
    }
    const double difference = std::abs(static_cast<double>(push[index]) -
                                       static_cast<double>(expected[index]));
    maximum = std::max(maximum, difference);
    if (difference > abs_tolerance + rel_tolerance * std::abs(expected[index])) {
      passed = false;
    }
  }
  return @{
    @"id" : identifier,
    @"passed" : @(passed),
    @"maximum_abs_error" : @(maximum),
    @"frames" : @(push_frames + flush_frames),
    @"feature_bins" : @(bins),
    @"input_samples" : @(expected_samples),
  };
}

std::string ProductType() {
  size_t size = 0;
  if (sysctlbyname("hw.machine", nullptr, &size, nullptr, 0) != 0 || size == 0u) {
    throw std::runtime_error("cannot determine device product type");
  }
  std::vector<char> value(size);
  if (sysctlbyname("hw.machine", value.data(), &size, nullptr, 0) != 0 ||
      value.empty() || value.back() != '\0') {
    throw std::runtime_error("cannot read device product type");
  }
  return std::string(value.data());
}

RunResult Execute() {
  const uint16_t endian_probe = 1u;
  if (*reinterpret_cast<const uint8_t*>(&endian_probe) != 1u) {
    throw std::runtime_error("little-endian target required");
  }
  if (vf_frontend_abi_version() != VF_FRONTEND_ABI_VERSION) {
    throw std::runtime_error("frontend ABI version differs from the public header");
  }
  NSData* raw = [NSData dataWithContentsOfFile:Resource(@"cases.json")];
  if (raw == nil) {
    throw std::runtime_error("cannot read cases manifest");
  }
  NSError* error = nil;
  id parsed = [NSJSONSerialization JSONObjectWithData:raw options:0 error:&error];
  if (error != nil || ![parsed isKindOfClass:NSDictionary.class]) {
    throw std::runtime_error("cannot parse cases manifest");
  }
  NSDictionary* root = parsed;
  if (![root[@"schema"] isEqual:@"aiii.voice.frontend.ios-cases"] ||
      ![root[@"schema_version"] isEqual:@1] ||
      ![root[@"cases"] isKindOfClass:NSArray.class] ||
      [root[@"cases"] count] == 0) {
    throw std::runtime_error("cases manifest schema is unsupported");
  }
  NSMutableArray* records = [NSMutableArray array];
  bool passed = true;
  for (id raw_case in root[@"cases"]) {
    if (![raw_case isKindOfClass:NSDictionary.class]) {
      throw std::runtime_error("case must be an object");
    }
    NSDictionary* record = ExecuteCase(raw_case);
    [records addObject:record];
    passed = passed && [record[@"passed"] boolValue];
  }
  UIDevice* device = UIDevice.currentDevice;
  NSDictionary* result = @{
    @"schema" : @"aiii.voice.frontend.ios-device-output",
    @"schema_version" : @1,
    @"target_id" : @"iphone-17-pro",
    @"abi_version" : @(vf_frontend_abi_version()),
    @"passed" : @(passed),
    @"records" : records,
    @"runtime" : @{
      @"backend" : @"ios-native-c99",
      @"system_name" : device.systemName,
      @"system_version" : device.systemVersion,
      @"product_type" : [NSString stringWithUTF8String:ProductType().c_str()],
    },
  };
  NSData* json = [NSJSONSerialization dataWithJSONObject:result options:0 error:&error];
  if (error != nil || json == nil) {
    throw std::runtime_error("cannot encode result JSON");
  }
  return {passed, std::string(static_cast<const char*>(json.bytes), json.length)};
}

}  // namespace

@interface AppDelegate : UIResponder <UIApplicationDelegate>
@property(strong, nonatomic) UIWindow* window;
@end

@implementation AppDelegate
- (BOOL)application:(UIApplication*)application
    didFinishLaunchingWithOptions:(NSDictionary*)launchOptions {
  self.window = [[UIWindow alloc] initWithFrame:UIScreen.mainScreen.bounds];
  self.window.rootViewController = [[UIViewController alloc] init];
  self.window.backgroundColor = UIColor.blackColor;
  [self.window makeKeyAndVisible];
  dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
    @autoreleasepool {
      try {
        const auto result = Execute();
        fprintf(stderr, "AII_VOICE_FRONTEND_RESULT %s\n", result.json.c_str());
        fflush(stderr);
        exit(result.passed ? 0 : 1);
      } catch (const std::exception& error) {
        fprintf(stderr, "AII_VOICE_FRONTEND_ERROR %s\n", error.what());
        fflush(stderr);
        exit(1);
      }
    }
  });
  return YES;
}
@end

int main(int argc, char* argv[]) {
  @autoreleasepool {
    return UIApplicationMain(argc, argv, nil, NSStringFromClass(AppDelegate.class));
  }
}
