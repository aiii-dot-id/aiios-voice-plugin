#pragma once
#include <jni.h>
#include <atomic>
#include <memory>
#include "session.h"
namespace aii::voice {
std::unique_ptr<Recognizer> make_java_prefix(JNIEnv*,jobject,std::vector<float>,
                                            std::shared_ptr<std::atomic<bool>> close_failed);
}
