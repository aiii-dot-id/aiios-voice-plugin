#pragma once
#include <jni.h>
#include <atomic>
#include <memory>
#include "session.h"
namespace aii::voice {
// Private Android embedding factory. Callback ownership retires on the same
// native inference thread; the caller must check teardown before reporting it.
std::unique_ptr<Recognizer> make_java_prefix(JNIEnv*,jobject,std::vector<float>,
                                            std::shared_ptr<std::atomic<bool>> close_failed);
}
