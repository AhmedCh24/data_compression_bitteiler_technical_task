cmake_minimum_required(VERSION 3.16)
project(freertos_sensor_project LANGUAGES C)
set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)

set(FREERTOS_PORT GCC_POSIX CACHE STRING "FreeRTOS POSIX port")
set(FREERTOS_HEAP 4 CACHE STRING "FreeRTOS heap implementation")

set(APP_DIR ${CMAKE_CURRENT_SOURCE_DIR}/scripts/main_task)
# Absolute data directory, so sensor_monitor works from any working directory.
set(APP_DATA_DIR "${CMAKE_CURRENT_SOURCE_DIR}/data/")

# The codec test is deliberately buildable without cloning the FreeRTOS kernel.
if(EXISTS "${APP_DIR}/tests/test_compression.c")
    add_executable(codec_test ${APP_DIR}/tests/test_compression.c ${APP_DIR}/src/compression.c)
    target_include_directories(codec_test PRIVATE ${APP_DIR}/include)
    target_link_libraries(codec_test PRIVATE m)
    target_compile_options(codec_test PRIVATE -Wall -Wextra -Wpedantic)
    enable_testing()
    add_test(NAME codec_test COMMAND codec_test)
endif()

if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/FreeRTOS-Kernel/CMakeLists.txt")
    add_library(freertos_config INTERFACE)
    target_include_directories(freertos_config SYSTEM INTERFACE ${APP_DIR}/include)
    target_compile_definitions(freertos_config INTERFACE projCOVERAGE_TEST=0)
    add_subdirectory(${CMAKE_CURRENT_SOURCE_DIR}/FreeRTOS-Kernel)

    add_executable(sensor_monitor
        ${APP_DIR}/src/main.c ${APP_DIR}/src/sensor_io.c ${APP_DIR}/src/sensor_tasks.c
        ${APP_DIR}/src/ingestion_task.c ${APP_DIR}/src/udp_task.c ${APP_DIR}/src/compression.c
        ${APP_DIR}/src/health_task.c ${APP_DIR}/src/rt_stats.c)
    target_include_directories(sensor_monitor PRIVATE ${APP_DIR}/include)
    target_compile_definitions(sensor_monitor PRIVATE APP_DATA_DIR="${APP_DATA_DIR}")
    find_package(Threads REQUIRED)
    target_link_libraries(sensor_monitor PRIVATE freertos_kernel Threads::Threads m)
    target_compile_options(sensor_monitor PRIVATE -Wall -Wextra -Wpedantic)
else()
    message(WARNING "FreeRTOS-Kernel not found: building codec_test only. Clone FreeRTOS-Kernel into the project root to build sensor_monitor.")
endif()
