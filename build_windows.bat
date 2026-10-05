@echo off
setlocal
REM Double-click this file, or set PYTHON_EXE to a full Python/Tk first.
cd /d "%~dp0"

REM Respect a caller's choice (useful for virtual environments and other PCs).
if not defined PYTHON_EXE (
  if exist "%LocalAppData%\Programs\Python\Python314\python.exe" (
    set "PYTHON_EXE=%LocalAppData%\Programs\Python\Python314\python.exe"
  ) else (
    set "PYTHON_EXE=python"
  )
)

"%PYTHON_EXE%" -c "import tkinter; tkinter.Tcl(); import customtkinter, PIL, PyInstaller"
if errorlevel 1 (
  echo.
  echo Use a full Python installation with Tcl/Tk and install requirements.txt.
  echo To choose Python: set "PYTHON_EXE=C:\path\to\python.exe"
  exit /b 1
)

REM One build entry point handles metadata, resources, notices, ZIP and SHA256.
REM Close a running LoveExporter.exe before rebuilding it.
"%PYTHON_EXE%" scripts\build_app.py
if errorlevel 1 exit /b %errorlevel%
echo.
echo Ready to run: "%cd%\dist\LoveExporter.exe"
endlocal
