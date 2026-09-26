cmake_minimum_required(VERSION 3.20)
project(Ninput LANGUAGES CXX)

# Static CRT everywhere (including the fetched safetyhook/zydis): the shipped DLLs load into an old
# game with no dependency on the MSVC runtime redistributable. Matches the loader and the plugins.
cmake_policy(SET CMP0091 NEW)
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded")

# SafetyHook (Boost 1.0) provides the trampolines; it pulls Zydis (MIT) itself. Pinned to the
# same commit vendored under vendor/safetyhook so the build is reproducible. Network is needed
# once to fetch it.
include(FetchContent)
FetchContent_Declare(
    safetyhook
    GIT_REPOSITORY https://github.com/cursey/safetyhook.git
    GIT_TAG        f44cc070a8340f2f26649553c49533475417304d
)
FetchContent_MakeAvailable(safetyhook)

set(DEF_DIR "${CMAKE_CURRENT_SOURCE_DIR}/def")

# Core shared by every personality: the hook arbiter, the D3D9 display arbiter, and the loader
# (DllMain, game detect, interface, plugin loading). Compiled once; its usage requirements
# (includes, C++23, safetyhook) propagate to whatever links it.
add_library(ninput_core OBJECT src/hook_registry.cpp src/display_arbiter.cpp src/input_engine.cpp src/ninput_core.cpp)
target_include_directories(ninput_core PUBLIC include src)
target_compile_features(ninput_core PUBLIC cxx_std_23)
target_link_libraries(ninput_core PUBLIC safetyhook)

# One SHARED DLL per proxy personality: core + its export forwarders + its .def (which sets the
# export names/ordinals) + OUTPUT_NAME (the file the game loads).
function(ninput_personality target proxy_src def outname)
    add_library(${target} SHARED src/${proxy_src})
    target_link_libraries(${target} PRIVATE ninput_core safetyhook kernel32 user32)
    set_target_properties(${target} PROPERTIES
        OUTPUT_NAME "${outname}"
        LINK_FLAGS "/DEF:\"${DEF_DIR}/${def}\"")
endfunction()

ninput_personality(ninput_xinput1_3 proxy_xinput.cpp   xinput1_3.def xinput1_3)
ninput_personality(ninput_dinput8    proxy_dinput8.cpp  dinput8.def   dinput8)
ninput_personality(ninput_version    proxy_version.cpp  version.def   version)

# Offline harnesses (no game). Each links the shared core.
#   registry_test: pillar 6, the hook arbiter.
#   display_test:  pillar 2, the D3D9 reset arbiter.
#   proxy_harness + marker_plugin: pillars 1/3, proxy forwarding + SDK handshake.
add_executable(ninput_registry_test test/registry_test.cpp)
target_link_libraries(ninput_registry_test PRIVATE ninput_core safetyhook kernel32 user32)

add_executable(ninput_display_test test/display_test.cpp)
target_link_libraries(ninput_display_test PRIVATE ninput_core safetyhook d3d9 kernel32 user32 gdi32)

# The Riftstone loader's [d3d9] chain followed: chain_test drives a stand-in d3d9.dll (the loader's own test
# DLL, which hands out Windows' Direct3D 9) named in a riftstone_loader.ini.
add_library(ninput_chain_d3d9 SHARED ../loader/test/chain_d3d9.cpp ../loader/test/chain_d3d9.def)
set_target_properties(ninput_chain_d3d9 PROPERTIES OUTPUT_NAME "chain_d3d9")
add_executable(ninput_chain_test test/chain_test.cpp)
target_link_libraries(ninput_chain_test PRIVATE ninput_core safetyhook d3d9 kernel32 user32)
add_dependencies(ninput_chain_test ninput_chain_d3d9)

add_executable(ninput_engine_test test/engine_test.cpp)
target_link_libraries(ninput_engine_test PRIVATE ninput_core safetyhook kernel32 user32)

add_executable(ninput_input_test test/input_test.cpp)
target_link_libraries(ninput_input_test PRIVATE ninput_core safetyhook kernel32 user32)

add_executable(ninput_proxy_harness test/proxy_harness.cpp)
target_compile_features(ninput_proxy_harness PRIVATE cxx_std_17)

add_library(ninput_marker_plugin SHARED test/marker_plugin.cpp)
target_include_directories(ninput_marker_plugin PRIVATE include)
target_compile_features(ninput_marker_plugin PRIVATE cxx_std_17)
set_target_properties(ninput_marker_plugin PROPERTIES OUTPUT_NAME "marker_plugin")

# The verified enemy_cap / lod_tuner plugins, built here as Ninput providers. They also run
# standalone via their own build.cmd; the Ninput handshake is additive and optional. Raw byte
# patching + naked thunks, x86, static CRT (matching their shippable build) -- no safetyhook.
function(ninput_provider_plugin target src outname)
    add_library(${target} SHARED ${src})
    target_include_directories(${target} PRIVATE include)
    target_compile_features(${target} PRIVATE cxx_std_17)
    set_target_properties(${target} PROPERTIES OUTPUT_NAME "${outname}" SUFFIX ".asi"
        LINK_FLAGS "/SAFESEH:NO" MSVC_RUNTIME_LIBRARY "MultiThreaded")
    target_link_libraries(${target} PRIVATE kernel32)
endfunction()
ninput_provider_plugin(ninput_enemy_cap ../plugins/enemy_cap/src/enemy_cap.cpp enemy_cap)
ninput_provider_plugin(ninput_lod_tuner ../plugins/lod_tuner/src/lod_tuner.cpp lod_tuner)

# Loads the real .asi files and drives them through the engine API (pillar 5 wiring).
add_executable(ninput_provider_test test/provider_test.cpp)
target_link_libraries(ninput_provider_test PRIVATE ninput_core safetyhook kernel32 user32)
add_dependencies(ninput_provider_test ninput_enemy_cap ninput_lod_tuner)
