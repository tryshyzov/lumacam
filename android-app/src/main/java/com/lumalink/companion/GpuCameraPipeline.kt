package com.lumalink.companion

import android.graphics.SurfaceTexture
import android.opengl.EGL14
import android.opengl.EGLConfig
import android.opengl.EGLContext
import android.opengl.EGLDisplay
import android.opengl.EGLExt
import android.opengl.EGLSurface
import android.opengl.GLES11Ext
import android.opengl.GLES20
import android.opengl.Matrix
import android.os.Handler
import android.os.HandlerThread
import android.util.Log
import android.util.Size
import android.view.Surface
import androidx.camera.core.CameraEffect
import androidx.camera.core.SurfaceOutput
import androidx.camera.core.SurfaceProcessor
import androidx.camera.core.SurfaceRequest
import androidx.core.util.Consumer
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.FloatBuffer
import java.util.concurrent.Executor

/**
 * Feeds camera frames to the H.264 encoder on the GPU, without copying them
 * through the CPU.
 *
 * A CameraX effect on the preview hands every camera frame to this processor
 * as an OpenGL texture. Each frame is drawn twice: into the on-screen preview,
 * with the rotation CameraX asks for, and into the encoder's input surface,
 * as the camera buffer is (the orientation the copying path sends too, which
 * the desktop turns by 180°). Everything runs on one GL thread.
 */
internal class GpuCameraPipeline(
    private val frames: Frames,
    private val onError: (Throwable) -> Unit,
) : SurfaceProcessor {
    /** What the pipeline needs from the app, called on the GL thread. */
    interface Frames {
        /** The encoder to draw a camera frame of [size] into, or null while not streaming. */
        fun encoderFor(size: Size): H264Encoder?

        /** Whether the frame taken at [timestampNs] should be encoded. */
        fun wantsFrame(timestampNs: Long): Boolean

        /** Called after a frame was drawn into [encoder]'s input. */
        fun frameEncoded(encoder: H264Encoder)
    }

    private val thread = HandlerThread("LumaLink GPU").apply { start() }
    private val handler = Handler(thread.looper)
    private val executor = Executor { handler.post(it) }

    val effect: CameraEffect =
        object : CameraEffect(CameraEffect.PREVIEW, executor, this, Consumer<Throwable> { onError(it) }) {}

    private var display: EGLDisplay = EGL14.EGL_NO_DISPLAY
    private var context: EGLContext = EGL14.EGL_NO_CONTEXT
    private var config: EGLConfig? = null
    private var idleSurface: EGLSurface = EGL14.EGL_NO_SURFACE
    private var program = 0
    private var texture = 0

    private var cameraTexture: SurfaceTexture? = null
    private var cameraSize = Size(0, 0)
    private val textureMatrix = FloatArray(16)
    private val previewMatrix = FloatArray(16)
    private val encoderMatrix = FloatArray(16)
    @Volatile private var bufferCorrection = FloatArray(16).also { flipForGl(it) }

    /**
     * Tell the pipeline about the camera being bound. The camera rotates (and,
     * for the front one, mirrors) what it hands to OpenGL to match the screen;
     * the encoder gets the buffer as the camera wrote it instead, like the
     * copying path. This undoes that, the way CameraX computes it for its own
     * outputs (SurfaceOutputImpl): predict the texture transform from the
     * sensor orientation and lens, invert it, and keep only the flip OpenGL needs.
     */
    fun setCamera(sensorRotationDegrees: Int, frontFacing: Boolean) {
        val predicted = FloatArray(16)
        flipForGl(predicted)
        Matrix.translateM(predicted, 0, 0.5f, 0.5f, 0f)
        Matrix.rotateM(predicted, 0, sensorRotationDegrees.toFloat(), 0f, 0f, 1f)
        Matrix.translateM(predicted, 0, -0.5f, -0.5f, 0f)
        if (frontFacing) {
            Matrix.translateM(predicted, 0, 1f, 0f, 0f)
            Matrix.scaleM(predicted, 0, -1f, 1f, 1f)
        }
        val inverted = FloatArray(16)
        Matrix.invertM(inverted, 0, predicted, 0)
        val flip = FloatArray(16).also { flipForGl(it) }
        val correction = FloatArray(16)
        Matrix.multiplyMM(correction, 0, inverted, 0, flip, 0)
        bufferCorrection = correction
    }

    private var preview: SurfaceOutput? = null
    private var previewSurface: EGLSurface = EGL14.EGL_NO_SURFACE
    private var encoder: H264Encoder? = null
    private var encoderSurface: EGLSurface = EGL14.EGL_NO_SURFACE

    override fun onInputSurface(request: SurfaceRequest) {
        handler.post {
            try {
                setUpGl()
                val size = request.resolution
                val surfaceTexture = SurfaceTexture(texture).apply {
                    setDefaultBufferSize(size.width, size.height)
                }
                val surface = Surface(surfaceTexture)
                cameraTexture?.release()
                cameraTexture = surfaceTexture
                cameraSize = size
                request.provideSurface(surface, executor) {
                    surfaceTexture.setOnFrameAvailableListener(null)
                    surfaceTexture.release()
                    surface.release()
                    if (cameraTexture === surfaceTexture) cameraTexture = null
                }
                surfaceTexture.setOnFrameAvailableListener({ drawFrame(it) }, handler)
                Log.i(TAG, "Camera frames arrive on the GPU at ${size.width}x${size.height}")
            } catch (error: Exception) {
                Log.e(TAG, "Could not take the camera's frames", error)
                request.willNotProvideSurface()
                onError(error)
            }
        }
    }

    override fun onOutputSurface(output: SurfaceOutput) {
        handler.post {
            try {
                setUpGl()
                val surface = output.getSurface(executor) {
                    if (preview === output) {
                        destroySurface(previewSurface)
                        previewSurface = EGL14.EGL_NO_SURFACE
                        preview = null
                    }
                    output.close()
                }
                destroySurface(previewSurface)
                preview = output
                previewSurface = windowSurface(surface)
            } catch (error: Exception) {
                Log.e(TAG, "Could not draw the preview", error)
                output.close()
                onError(error)
            }
        }
    }

    /** Stop and free the GL thread; [onReleased] runs on it first. */
    fun release(onReleased: () -> Unit = {}) {
        handler.post {
            onReleased()
            destroySurface(previewSurface)
            destroySurface(encoderSurface)
            previewSurface = EGL14.EGL_NO_SURFACE
            encoderSurface = EGL14.EGL_NO_SURFACE
            cameraTexture?.release()
            cameraTexture = null
            if (display != EGL14.EGL_NO_DISPLAY) {
                EGL14.eglMakeCurrent(display, EGL14.EGL_NO_SURFACE, EGL14.EGL_NO_SURFACE, EGL14.EGL_NO_CONTEXT)
                destroySurface(idleSurface)
                EGL14.eglDestroyContext(display, context)
                EGL14.eglTerminate(display)
            }
            display = EGL14.EGL_NO_DISPLAY
            thread.quitSafely()
        }
    }

    private fun drawFrame(surfaceTexture: SurfaceTexture) {
        if (surfaceTexture !== cameraTexture) return
        try {
            surfaceTexture.updateTexImage()
            surfaceTexture.getTransformMatrix(textureMatrix)
            val timestamp = surfaceTexture.timestamp

            val output = preview
            if (output != null && previewSurface != EGL14.EGL_NO_SURFACE) {
                output.updateTransformMatrix(previewMatrix, textureMatrix)
                draw(previewSurface, output.size, previewMatrix)
                EGL14.eglSwapBuffers(display, previewSurface)
            }

            val target = frames.encoderFor(cameraSize)
            if (target !== encoder) {
                destroySurface(encoderSurface)
                encoderSurface = target?.inputSurface?.let { windowSurface(it) } ?: EGL14.EGL_NO_SURFACE
                encoder = target
            }
            if (target != null && encoderSurface != EGL14.EGL_NO_SURFACE && frames.wantsFrame(timestamp)) {
                Matrix.multiplyMM(encoderMatrix, 0, textureMatrix, 0, bufferCorrection, 0)
                draw(encoderSurface, cameraSize, encoderMatrix)
                EGLExt.eglPresentationTimeANDROID(display, encoderSurface, timestamp)
                EGL14.eglSwapBuffers(display, encoderSurface)
                frames.frameEncoded(target)
            }
        } catch (error: Exception) {
            Log.e(TAG, "Could not draw a camera frame", error)
        }
    }

    private fun draw(surface: EGLSurface, size: Size, matrix: FloatArray) {
        EGL14.eglMakeCurrent(display, surface, surface, context)
        GLES20.glViewport(0, 0, size.width, size.height)
        GLES20.glUseProgram(program)
        GLES20.glActiveTexture(GLES20.GL_TEXTURE0)
        GLES20.glBindTexture(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, texture)
        GLES20.glUniformMatrix4fv(GLES20.glGetUniformLocation(program, "uTextureMatrix"), 1, false, matrix, 0)
        val position = GLES20.glGetAttribLocation(program, "aPosition")
        GLES20.glEnableVertexAttribArray(position)
        GLES20.glVertexAttribPointer(position, 2, GLES20.GL_FLOAT, false, 0, QUAD)
        GLES20.glDrawArrays(GLES20.GL_TRIANGLE_STRIP, 0, 4)
        GLES20.glDisableVertexAttribArray(position)
    }

    private fun setUpGl() {
        if (display != EGL14.EGL_NO_DISPLAY) return
        display = EGL14.eglGetDisplay(EGL14.EGL_DEFAULT_DISPLAY)
        val version = IntArray(2)
        check(EGL14.eglInitialize(display, version, 0, version, 1)) { "EGL is unavailable" }
        val attributes = intArrayOf(
            EGL14.EGL_RED_SIZE, 8,
            EGL14.EGL_GREEN_SIZE, 8,
            EGL14.EGL_BLUE_SIZE, 8,
            EGL14.EGL_ALPHA_SIZE, 8,
            EGL14.EGL_RENDERABLE_TYPE, EGL14.EGL_OPENGL_ES2_BIT,
            EGL14.EGL_SURFACE_TYPE, EGL14.EGL_WINDOW_BIT or EGL14.EGL_PBUFFER_BIT,
            EGL_RECORDABLE_ANDROID, 1,
            EGL14.EGL_NONE,
        )
        val configs = arrayOfNulls<EGLConfig>(1)
        val count = IntArray(1)
        check(EGL14.eglChooseConfig(display, attributes, 0, configs, 0, 1, count, 0) && count[0] > 0) {
            "No EGL configuration can feed a video encoder"
        }
        config = configs[0]
        context = EGL14.eglCreateContext(
            display, config, EGL14.EGL_NO_CONTEXT,
            intArrayOf(EGL14.EGL_CONTEXT_CLIENT_VERSION, 2, EGL14.EGL_NONE), 0,
        )
        check(context != EGL14.EGL_NO_CONTEXT) { "Could not create an OpenGL context" }
        idleSurface = EGL14.eglCreatePbufferSurface(
            display, config, intArrayOf(EGL14.EGL_WIDTH, 1, EGL14.EGL_HEIGHT, 1, EGL14.EGL_NONE), 0,
        )
        EGL14.eglMakeCurrent(display, idleSurface, idleSurface, context)

        program = linkProgram(VERTEX_SHADER, FRAGMENT_SHADER)
        val names = IntArray(1)
        GLES20.glGenTextures(1, names, 0)
        texture = names[0]
        GLES20.glBindTexture(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, texture)
        GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_MIN_FILTER, GLES20.GL_LINEAR)
        GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_MAG_FILTER, GLES20.GL_LINEAR)
        GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_WRAP_S, GLES20.GL_CLAMP_TO_EDGE)
        GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_WRAP_T, GLES20.GL_CLAMP_TO_EDGE)
    }

    private fun windowSurface(surface: Surface): EGLSurface {
        val eglSurface = EGL14.eglCreateWindowSurface(display, config, surface, intArrayOf(EGL14.EGL_NONE), 0)
        check(eglSurface != EGL14.EGL_NO_SURFACE) { "Could not draw into a surface" }
        return eglSurface
    }

    private fun destroySurface(surface: EGLSurface) {
        if (surface == EGL14.EGL_NO_SURFACE || display == EGL14.EGL_NO_DISPLAY) return
        EGL14.eglMakeCurrent(display, idleSurface, idleSurface, context)
        EGL14.eglDestroySurface(display, surface)
    }

    private fun linkProgram(vertexSource: String, fragmentSource: String): Int {
        fun compile(type: Int, source: String): Int {
            val shader = GLES20.glCreateShader(type)
            GLES20.glShaderSource(shader, source)
            GLES20.glCompileShader(shader)
            val status = IntArray(1)
            GLES20.glGetShaderiv(shader, GLES20.GL_COMPILE_STATUS, status, 0)
            check(status[0] != 0) { "Shader: ${GLES20.glGetShaderInfoLog(shader)}" }
            return shader
        }
        val linked = GLES20.glCreateProgram()
        GLES20.glAttachShader(linked, compile(GLES20.GL_VERTEX_SHADER, vertexSource))
        GLES20.glAttachShader(linked, compile(GLES20.GL_FRAGMENT_SHADER, fragmentSource))
        GLES20.glLinkProgram(linked)
        val status = IntArray(1)
        GLES20.glGetProgramiv(linked, GLES20.GL_LINK_STATUS, status, 0)
        check(status[0] != 0) { "Program: ${GLES20.glGetProgramInfoLog(linked)}" }
        return linked
    }

    private companion object {
        const val TAG = "LumaLinkGpu"

        /** Identity with the vertical flip between image rows and GL texture coordinates. */
        fun flipForGl(matrix: FloatArray) {
            Matrix.setIdentityM(matrix, 0)
            Matrix.translateM(matrix, 0, 0f, 0.5f, 0f)
            Matrix.scaleM(matrix, 0, 1f, -1f, 1f)
            Matrix.translateM(matrix, 0, 0f, -0.5f, 0f)
        }
        const val EGL_RECORDABLE_ANDROID = 0x3142

        // A full-screen quad; the texture matrix maps it onto the camera buffer.
        val QUAD: FloatBuffer = ByteBuffer.allocateDirect(8 * 4)
            .order(ByteOrder.nativeOrder())
            .asFloatBuffer()
            .apply {
                put(floatArrayOf(-1f, -1f, 1f, -1f, -1f, 1f, 1f, 1f))
                position(0)
            }

        const val VERTEX_SHADER = """
            attribute vec2 aPosition;
            uniform mat4 uTextureMatrix;
            varying vec2 vTextureCoordinate;
            void main() {
                gl_Position = vec4(aPosition, 0.0, 1.0);
                vTextureCoordinate = (uTextureMatrix * vec4((aPosition + 1.0) * 0.5, 0.0, 1.0)).xy;
            }
        """

        const val FRAGMENT_SHADER = """
            #extension GL_OES_EGL_image_external : require
            precision mediump float;
            uniform samplerExternalOES uTexture;
            varying vec2 vTextureCoordinate;
            void main() {
                gl_FragColor = texture2D(uTexture, vTextureCoordinate);
            }
        """
    }
}
