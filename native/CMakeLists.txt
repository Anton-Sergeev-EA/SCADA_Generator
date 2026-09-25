cmake_minimum_required(VERSION 3.18)
project(scada_native LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

option(SCADA_BUILD_PYTHON "Build pybind11 module scada_core.ml._native" ON)
option(SCADA_BUILD_TESTS "Build C++ unit tests" ON)

if(MSVC)
  set(SCADA_WARNINGS /W4 /permissive-)
else()
  set(SCADA_WARNINGS -Wall -Wextra -Wpedantic -Wconversion -Wshadow)
endif()

add_library(scada_analytics STATIC
  src/streaming_detector.cpp
  src/holt_forecaster.cpp)
target_include_directories(scada_analytics PUBLIC include)
target_compile_options(scada_analytics PRIVATE ${SCADA_WARNINGS})

if(SCADA_BUILD_TESTS)
  enable_testing()
  add_executable(scada_native_tests tests/test_core.cpp)
  target_link_libraries(scada_native_tests PRIVATE scada_analytics)
  target_compile_options(scada_native_tests PRIVATE ${SCADA_WARNINGS})
  add_test(NAME scada_native_tests COMMAND scada_native_tests)
endif()

if(SCADA_BUILD_PYTHON)
  find_package(Python3 COMPONENTS Interpreter Development.Module REQUIRED)
  if(NOT pybind11_DIR)
    execute_process(
      COMMAND ${Python3_EXECUTABLE} -m pybind11 --cmakedir
      OUTPUT_VARIABLE pybind11_DIR OUTPUT_STRIP_TRAILING_WHITESPACE)
  endif()
  find_package(pybind11 CONFIG REQUIRED)
  pybind11_add_module(_native src/bindings.cpp)
  target_link_libraries(_native PRIVATE scada_analytics)
  # Модуль кладём прямо в пакет, чтобы `import scada_core.ml._native` работал без установки.
  set_target_properties(_native PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/../scada_core/ml
    LIBRARY_OUTPUT_DIRECTORY_RELEASE ${CMAKE_CURRENT_SOURCE_DIR}/../scada_core/ml)
endif()
