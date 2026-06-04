#pragma once
#define LW_PREFIX "CBCM Simulator"
#if __has_include("lwlog_core.hpp")
#include "lwlog_core.hpp"
#else
#define __LW_TRACE(...) do {} while (0)
#define __LW_DEBUG(...) do {} while (0)
#define __LW_INFO(...) do {} while (0)
#define __LW_ERROR(...) do {} while (0)
#endif

#define CBCM_TRACE(fmt, ...) __LW_TRACE(LW_PREFIX, fmt, ##__VA_ARGS__)
#define CBCM_DEBUG(fmt, ...) __LW_DEBUG(LW_PREFIX, fmt, ##__VA_ARGS__)
#define CBCM_INFO(fmt, ...)  __LW_INFO (LW_PREFIX, fmt, ##__VA_ARGS__)
#define CBCM_ERROR(fmt, ...) __LW_ERROR(LW_PREFIX, fmt, ##__VA_ARGS__)
