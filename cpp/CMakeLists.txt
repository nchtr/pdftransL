cmake_minimum_required(VERSION 3.20)
project(pdftransl VERSION 0.20.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)
set(CMAKE_AUTORCC ON)
set(CMAKE_AUTOUIC ON)

find_package(Qt6 REQUIRED COMPONENTS Widgets Network Sql Concurrent)
find_package(SQLite3 QUIET)

option(USE_MUPDF "Enable native MuPDF PDF parsing" OFF)

if(USE_MUPDF)
    find_library(MUPDF_LIB mupdf)
    find_path(MUPDF_INCLUDE mupdf/fitz.h)
    if(MUPDF_LIB AND MUPDF_INCLUDE)
        add_compile_definitions(HAVE_MUPDF)
    else()
        message(WARNING "MuPDF not found, native PDF parsing disabled")
        set(USE_MUPDF OFF)
    endif()
endif()

set(CORE_SOURCES
    src/core/config.cpp
    src/core/models.cpp
    src/pipeline/desktop_pipeline.cpp
)

set(LLM_SOURCES
    src/llm/openai_client.cpp
    src/llm/anthropic_client.cpp
    src/llm/fallback_client.cpp
    src/llm/ratelimit.cpp
)

set(PARSING_SOURCES
    src/parsing/splitter.cpp
    src/parsing/subprocess_backend.cpp
)

set(TRANSLATION_SOURCES
    src/translation/translator.cpp
    src/translation/masker.cpp
    src/translation/prompts.cpp
    src/translation/checkpoint.cpp
)

set(RAG_SOURCES
    src/rag/store.cpp
    src/rag/glossary.cpp
    src/rag/embeddings.cpp
)

set(EXPORT_SOURCES
    src/export/exporter.cpp
    src/export/html_export.cpp
    src/export/latex_export.cpp
)

set(QUALITY_SOURCES
    src/quality/validators.cpp
    src/quality/reviewer.cpp
)

set(GUI_SOURCES
    src/gui/mainwindow.cpp
    src/gui/uploadwidget.cpp
    src/gui/joblistwidget.cpp
    src/gui/jobdetailwidget.cpp
    src/gui/settingswidget.cpp
    src/gui/glossarywidget.cpp
)

add_executable(pdftransl
    src/main.cpp
    ${CORE_SOURCES}
    ${LLM_SOURCES}
    ${PARSING_SOURCES}
    ${TRANSLATION_SOURCES}
    ${RAG_SOURCES}
    ${EXPORT_SOURCES}
    ${QUALITY_SOURCES}
    ${GUI_SOURCES}
)

target_include_directories(pdftransl PRIVATE src)
target_link_libraries(pdftransl PRIVATE
    Qt6::Widgets
    Qt6::Network
    Qt6::Sql
    Qt6::Concurrent
)

if(USE_MUPDF)
    target_include_directories(pdftransl PRIVATE ${MUPDF_INCLUDE})
    target_link_libraries(pdftransl PRIVATE ${MUPDF_LIB})
    target_sources(pdftransl PRIVATE src/parsing/mupdf_backend.cpp)
endif()

install(TARGETS pdftransl RUNTIME DESTINATION bin)
