// Offline packaging tool. Compilation never occurs in CoreMLSeparator.
#import <CoreML/CoreML.h>
#import <Foundation/Foundation.h>
#include <iostream>
#include <stdexcept>
int main(int argc,char** argv){@autoreleasepool{try{
  if(argc!=3)throw std::runtime_error("stage packages and new compiled directory required");
  NSFileManager* files=[NSFileManager defaultManager];NSError* error=nil;
  NSString* input=@(argv[1]);NSString* output=@(argv[2]);
  if([files fileExistsAtPath:output])throw std::runtime_error("compiled output already exists");
  if(![files createDirectoryAtPath:output withIntermediateDirectories:NO attributes:nil error:&error])
    throw std::runtime_error(error.localizedDescription.UTF8String);
  for(unsigned i=0;i<8;++i){
    NSString* source=[input stringByAppendingPathComponent:[NSString stringWithFormat:@"stage-%u.mlpackage",i]];
    NSURL* compiled=[MLModel compileModelAtURL:[NSURL fileURLWithPath:source] error:&error];
    if(!compiled)throw std::runtime_error(error.localizedDescription.UTF8String);
    NSURL* destination=[NSURL fileURLWithPath:[output stringByAppendingPathComponent:[NSString stringWithFormat:@"stage-%u.mlmodelc",i]]];
    if(![files copyItemAtURL:compiled toURL:destination error:&error])
      throw std::runtime_error(error.localizedDescription.UTF8String);
    // Only the fresh compiler-owned temporary object is removed, never input.
    if(![files removeItemAtURL:compiled error:&error])throw std::runtime_error(error.localizedDescription.UTF8String);
    std::cout<<"compiled stage "<<i<<std::endl;
  }return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<std::endl;return 1;}}}
