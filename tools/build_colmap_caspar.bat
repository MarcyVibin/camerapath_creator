@echo off
rem COLMAP 4.2.1 with CUDA 13.5 + CASPAR (double precision) GPU bundle adjustment, no GUI.
rem Needs: VS 2026 Build Tools (C++), CUDA 13.5, D:\vcpkg (cloned + bootstrapped),
rem        git clone --branch 4.2.1 https://github.com/colmap/colmap D:\colmap-src
rem COLMAP 4.2.1 bug: in D:\colmap-src\src\thirdparty\CMakeLists.txt change  /FI"${_MSVC_COMPACT_HEADER}"
rem   to  /FI${_MSVC_COMPACT_HEADER}  (the extra quotes get escaped and cl.exe can't find the header).
rem After the build, copy the DLLs:  copy D:\colmap-src\build\src\colmap\exe\*.dll D:\colmap-caspar\bin\
call "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvars64.bat" || exit /b 1
set "CUDA_PATH=C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.5"
rem VS's own CMake/Ninja first: C:\mingw64 versions are on PATH otherwise.
set "VSCM=C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\Common7\IDE\CommonExtensions\Microsoft\CMake"
set "PATH=%VSCM%\CMake\bin;%VSCM%\Ninja;%CUDA_PATH%\bin;%PATH%"
where cmake ninja
set VCPKG_DISABLE_METRICS=1
cd /d D:\colmap-src || exit /b 1

cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release ^
  -DCMAKE_TOOLCHAIN_FILE=D:/vcpkg/scripts/buildsystems/vcpkg.cmake -DVCPKG_TARGET_TRIPLET=x64-windows ^
  -DVCPKG_MANIFEST_NO_DEFAULT_FEATURES=ON "-DVCPKG_MANIFEST_FEATURES=download;cuda" ^
  -DCUDA_ENABLED=ON -DCMAKE_CUDA_ARCHITECTURES=86 ^
  "-DCMAKE_CUDA_COMPILER=%CUDA_PATH%/bin/nvcc.exe" "-DCUDAToolkit_ROOT=%CUDA_PATH%" ^
  "-DCMAKE_CUDA_FLAGS=-Xcompiler=/Zc:preprocessor" ^
  -DCASPAR_ENABLED=ON -DCASPAR_USE_DOUBLE=ON ^
  -DGUI_ENABLED=OFF -DONNX_ENABLED=OFF -DTESTS_ENABLED=OFF -DCGAL_ENABLED=OFF || exit /b 2

cmake --build build || exit /b 3
cmake --install build --prefix D:\colmap-caspar || exit /b 4
copy /y build\src\colmap\exe\*.dll D:\colmap-caspar\bin\ >nul || exit /b 5
echo BUILD_OK
