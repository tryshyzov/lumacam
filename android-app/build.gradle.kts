plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Release signing and versioning come from the environment (set by CI); without
// them the release build stays unsigned and the debug build is used instead.
fun environment(name: String): String? = System.getenv(name)?.takeIf { it.isNotBlank() }

val releaseKeystore = environment("LUMALINK_KEYSTORE_FILE")

android {
    namespace = "com.lumalink.companion"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.lumalink.companion"
        minSdk = 26
        targetSdk = 35
        versionCode = environment("LUMALINK_VERSION_CODE")?.toIntOrNull() ?: 1
        versionName = environment("LUMALINK_VERSION_NAME") ?: "0.1.0"
    }

    signingConfigs {
        if (releaseKeystore != null) {
            create("release") {
                storeFile = file(releaseKeystore)
                storePassword = environment("LUMALINK_KEYSTORE_PASSWORD")
                keyAlias = "lumalink"
                keyPassword = environment("LUMALINK_KEYSTORE_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.findByName("release")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    val cameraX = "1.4.2"
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.activity:activity-ktx:1.10.1")
    implementation("androidx.camera:camera-camera2:$cameraX")
    implementation("androidx.camera:camera-lifecycle:$cameraX")
    implementation("androidx.camera:camera-view:$cameraX")
    testImplementation("junit:junit:4.13.2")
    // Android's own org.json is only a stub in local unit tests.
    testImplementation("org.json:json:20240303")
}
