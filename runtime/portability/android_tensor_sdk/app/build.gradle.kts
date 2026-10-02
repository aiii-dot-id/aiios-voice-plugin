plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}
android {
    namespace = "id.aiii.voice.tensorsdkcheck"
    compileSdk = 36
    defaultConfig {
        applicationId = "id.aiii.voice.tensorsdkcheck"
        minSdk = 31
        targetSdk = 36
        versionCode = 1
        versionName = "2.2.0-sdk-check"
        ndk { abiFilters += "arm64-v8a" }
    }
    packaging { jniLibs { useLegacyPackaging = true } }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}
kotlin { jvmToolchain(17) }
dependencies { implementation("com.google.ai.edge.litert:litert:2.2.0") }
