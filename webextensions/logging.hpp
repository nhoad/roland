#include <atomic>
#include <iostream>


namespace logging
{
    std::atomic<int> level;

    size_t BuildPrefixInit()
    {
        const char *ThisFileNameTail = "io.hpp";
        const char *file=__FILE__;

        if (!strstr(file, ThisFileNameTail))
            return 0;

        return strlen(file)-strlen(ThisFileNameTail);
    }

    const char* SkipBuildPrefix(const char* path)
    {
        static const size_t BuildPrefixLength = BuildPrefixInit();

        return path+BuildPrefixLength;
    }
}

#define logger(_level, message) \
   do { \
        if (_level <= logging::level) { \
            std::time_t t = std::time(NULL); \
            char mbstr[100]; \
            if (std::strftime(mbstr, sizeof(mbstr), "%F %T", std::localtime(&t))) { \
                std::cout << '[' << mbstr << "] " \
                    << logging::SkipBuildPrefix(__FILE__) << "(" << __LINE__ << "): " \
                    << message << std::endl; \
            } \
        } \
   } while(0)


