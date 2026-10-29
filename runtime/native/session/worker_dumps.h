#pragma once
namespace aii::voice {
void disable_crash_dumps();
#ifdef _WIN32
const void* unreported_exception_filter();
#endif
}
