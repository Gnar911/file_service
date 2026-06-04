cmake_minimum_required(VERSION 3.18)
project(file_service_core LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_library(file_service_core SHARED
    can_parser.cpp
    can_decoder.cpp
    can_log_decoder.cpp
    mmap_wrapper.cpp
    mmap_header_constract.cpp
)

target_include_directories(file_service_core PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
)

if(NOT WIN32)
    find_package(Threads REQUIRED)
    target_link_libraries(file_service_core PRIVATE Threads::Threads)
endif()

set_target_properties(file_service_core PROPERTIES
    OUTPUT_NAME file_service_core
)
