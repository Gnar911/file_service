#ifndef QTRACELOG_H
#define QTRACELOG_H

#define LW_TRACE

#ifdef LW_TRACE
#include <lwlog_core.h>
#define LW_PREFIX "LWW"
#define LOGGING_TRACE_ENABLED1(...) __LW_TRACE_COUNT(__VA_ARGS__)
#define LOGGING_TRACE_ENABLED __LW_TRACE()
#else
#define LOGGING_TRACE_ENABLED ;
#define LOGGING_TRACE_ENABLED1(...) ;
#endif

#endif // QTRACELOG_H
