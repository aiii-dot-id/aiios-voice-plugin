#import <UIKit/UIKit.h>
#import <CommonCrypto/CommonDigest.h>
#include <fstream>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>

int aii_mobile_fixture_main(int,char**);

static bool VerifyInputs(NSString* root, NSUInteger expected=33, NSString* manifestName=@"manifest.json") {
  NSData* data=[NSData dataWithContentsOfFile:[root stringByAppendingPathComponent:manifestName]];
  if(!data)return false;
  NSDictionary* manifest=[NSJSONSerialization JSONObjectWithData:data options:0 error:nil];
  if(![manifest isKindOfClass:NSDictionary.class] || manifest.count!=expected)return false;
  for(NSString* name in manifest) {
    if([name hasPrefix:@"/"] || [[name pathComponents] containsObject:@".."])return false;
    std::ifstream f([root stringByAppendingPathComponent:name].fileSystemRepresentation,std::ios::binary);
    if(!f)return false;
    CC_SHA256_CTX hash;CC_SHA256_Init(&hash);char block[65536];uint64_t size=0;
    while(f) {f.read(block,sizeof block);const auto n=f.gcount();if(n>0){CC_SHA256_Update(&hash,block,CC_LONG(n));size+=n;}}
    if(!f.eof() || size!=[manifest[name][@"bytes"] unsignedLongLongValue])return false;
    unsigned char digest[CC_SHA256_DIGEST_LENGTH];CC_SHA256_Final(digest,&hash);
    NSMutableString* observed=[NSMutableString string];for(unsigned char c:digest)[observed appendFormat:@"%02x",c];
    if(![observed isEqualToString:manifest[name][@"sha256"]])return false;
  }
  printf("{\"kind\":\"input_inventory_verified\",\"files\":%lu}\n",(unsigned long)manifest.count);fflush(stdout);return true;
}

@interface VoiceApp : UIResponder <UIApplicationDelegate>
@property(strong,nonatomic) UIWindow *window;
@property(strong,nonatomic) UILabel *label;
@end
@implementation VoiceApp
- (BOOL)application:(UIApplication*)app didFinishLaunchingWithOptions:(NSDictionary*)options {
  (void)app;(void)options;
  self.window=[[UIWindow alloc] initWithFrame:UIScreen.mainScreen.bounds];
  UIViewController* controller=[UIViewController new];
  controller.view.backgroundColor=UIColor.systemBackgroundColor;
  self.label=[[UILabel alloc] initWithFrame:CGRectInset(self.window.bounds,24,80)];
  self.label.numberOfLines=0;self.label.text=@"AII Voice: native device test loading…";
  [controller.view addSubview:self.label];self.window.rootViewController=controller;[self.window makeKeyAndVisible];
  dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED,0),^{
    NSString* documents=NSSearchPathForDirectoriesInDomains(NSDocumentDirectory,NSUserDomainMask,YES).firstObject;
    NSString* root=[documents stringByAppendingPathComponent:@"fixture"];
    const char* rawRun=std::getenv("AII_MOBILE_RUN_ID");
    NSString* run=rawRun?[NSString stringWithUTF8String:rawRun]:@"run-r1";
    NSCharacterSet* invalid=[[NSCharacterSet characterSetWithCharactersInString:@"abcdefghijklmnopqrstuvwxyz0123456789-"] invertedSet];
    if(!run.length || run.length>64 || [run rangeOfCharacterFromSet:invalid].location!=NSNotFound)return;
    NSString* output=[documents stringByAppendingPathComponent:run];
    if([[NSFileManager defaultManager] fileExistsAtPath:output] || ![[NSFileManager defaultManager] createDirectoryAtPath:output withIntermediateDirectories:NO attributes:nil error:nil])return;
    NSString* log=[output stringByAppendingPathComponent:@"events.jsonl"];
    NSString* errors=[output stringByAppendingPathComponent:@"errors.txt"];
    int rc=2;
    const char* requestedMode=std::getenv("AII_MOBILE_PROBE");
    const bool ttsMode=false;
    if(freopen(log.fileSystemRepresentation,"wx",stdout) && freopen(errors.fileSystemRepresentation,"wx",stderr)) {
      setenv("ORT_DISABLE_TELEMETRY","1",1);
      setenv("AII_VOICE_STARTUP_TRACE","1",1);
      setenv("AII_MOBILE_PROFILE_DIR",output.fileSystemRepresentation,1);
      const char* rawBackend=std::getenv("AII_MOBILE_TTS_BACKEND");
      NSString* backend=rawBackend?[NSString stringWithUTF8String:rawBackend]:@"cpu";
      if(![backend isEqualToString:@"coreml-gpu"])return;
      NSArray* relative=@[@"models/stt",@"models/stt/mel.f32",@"models/vad/model.onnx",@"models/endpoint/model.onnx",
        @"models/endpoint/coefficients.f32",@"models/tts",@"models/tts/config.yaml",@"recovery.f32",@"audio",@"cpu",
        @"models/uid/model.onnx",@"uid-policy.json",@"empty-snapshot.json"];
      std::vector<std::string> strings{"AiiVoiceMobile"};
      for(NSString* name in relative)strings.emplace_back([name isEqualToString:@"cpu"]?backend.UTF8String:[([name isEqualToString:@"audio"]?output:root) stringByAppendingPathComponent:name].fileSystemRepresentation);
      NSString* coreml=[documents stringByAppendingPathComponent:@"coreml-cache"];
      strings[6]=coreml.fileSystemRepresentation;
      strings[7]=[coreml stringByAppendingPathComponent:@"binding.json"].fileSystemRepresentation;
      const char* mode=std::getenv("AII_MOBILE_PROBE");
      if(!mode || std::string(mode)!="conversation")return;
      std::vector<char*> argv;for(auto& s:strings)argv.push_back(s.data());
      if(VerifyInputs(root) && VerifyInputs(coreml,15,@"binding.json"))
        rc=aii_mobile_fixture_main(int(argv.size()),argv.data());
      else fprintf(stderr,"Fixture inventory missing or differs; no models loaded\n");
      fflush(stdout);fflush(stderr);
    }
    NSDictionary* verdict=@{@"exit_code":@(rc),@"probe":ttsMode?@"tts":@"conversation",@"physical_audio":@NO,@"human_level_qualified":@NO};
    NSData* data=[NSJSONSerialization dataWithJSONObject:verdict options:0 error:nil];
    [data writeToFile:[output stringByAppendingPathComponent:@"result.json"] atomically:YES];
    dispatch_async(dispatch_get_main_queue(),^{
      if(rc!=0)self.label.text=@"Native device test failed. Evidence saved; no release claim.";
      else if(ttsMode)self.label.text=@"TTS component controls passed. Numerical, full-conversation and physical-audio gates remain.";
      else self.label.text=@"Five native models passed the recorded conversation test. Physical audio and release gates remain.";
    });
  });
  return YES;
}
@end
int main(int argc,char** argv) { @autoreleasepool { return UIApplicationMain(argc,argv,nil,NSStringFromClass(VoiceApp.class)); } }
