// settings.gradle.kts (루트)
pluginManagement {
    repositories {
        google()
        mavenCentral()
        maven("https://jitpack.io")
        gradlePluginPortal()
    }
}

// JDK 자동 다운로드/설치용 툴체인 리졸버
plugins {
    id("org.gradle.toolchains.foojay-resolver-convention") version "0.8.0"
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
        maven("https://jitpack.io")
    }
}

// ✅ 프로젝트 표시 이름(Gradle/IDE)
rootProject.name = "InterfaceUI" // 프로젝트 표시 이름은 따로 수정

include(":app")
