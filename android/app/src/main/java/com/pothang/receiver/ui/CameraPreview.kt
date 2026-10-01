package com.pothang.receiver.ui

import android.Manifest
import android.content.pm.PackageManager
import android.util.Log
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.Camera
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.Preview
import androidx.camera.core.resolutionselector.ResolutionSelector
import androidx.camera.core.resolutionselector.ResolutionStrategy
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import android.util.Size
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.LocalLifecycleOwner
import com.pothang.receiver.ui.theme.FgMuted
import java.util.concurrent.Executors

/** Shows [content] once the camera permission is granted, else a prompt. */
@Composable
fun WithCameraPermission(modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    val context = LocalContext.current
    var granted by remember {
        mutableStateOf(ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA)
            == PackageManager.PERMISSION_GRANTED)
    }
    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {
        granted = it
    }
    LaunchedEffect(Unit) { if (!granted) launcher.launch(Manifest.permission.CAMERA) }
    if (granted) {
        content()
    } else {
        Column(
            modifier.fillMaxSize().padding(24.dp),
            verticalArrangement = Arrangement.Center,
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text("The camera is needed to read labels.", textAlign = TextAlign.Center, color = FgMuted)
            Button(onClick = { launcher.launch(Manifest.permission.CAMERA) },
                modifier = Modifier.padding(top = 12.dp)) { Text("Allow camera") }
        }
    }
}

/**
 * Back-camera preview with an [ImageAnalysis.Analyzer] attached. Rebinds only
 * when the analyzer changes; rotations just update target rotation so the
 * analyzer keeps receiving upright frames.
 */
@Composable
fun CameraPreview(
    analyzer: ImageAnalysis.Analyzer,
    torchOn: Boolean,
    modifier: Modifier = Modifier,
) {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    val configuration = LocalConfiguration.current
    val previewView = remember {
        PreviewView(context).apply {
            scaleType = PreviewView.ScaleType.FILL_CENTER
            implementationMode = PreviewView.ImplementationMode.COMPATIBLE
        }
    }
    val executor = remember { Executors.newSingleThreadExecutor() }
    var camera by remember { mutableStateOf<Camera?>(null) }
    val preview = remember { Preview.Builder().build() }
    val analysis = remember {
        ImageAnalysis.Builder()
            // ~1280x720 is plenty for label text and keeps OCR fast.
            .setResolutionSelector(
                ResolutionSelector.Builder()
                    .setResolutionStrategy(ResolutionStrategy(Size(1280, 720),
                        ResolutionStrategy.FALLBACK_RULE_CLOSEST_HIGHER_THEN_LOWER))
                    .build())
            .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
            .build()
    }

    DisposableEffect(lifecycleOwner, analyzer) {
        val future = ProcessCameraProvider.getInstance(context)
        var provider: ProcessCameraProvider? = null
        future.addListener({
            provider = future.get()
            try {
                preview.setSurfaceProvider(previewView.surfaceProvider)
                analysis.setAnalyzer(executor, analyzer)
                provider?.unbindAll()
                camera = provider?.bindToLifecycle(
                    lifecycleOwner, CameraSelector.DEFAULT_BACK_CAMERA, preview, analysis)
            } catch (e: Exception) {
                Log.e("CameraPreview", "Camera bind failed", e)
            }
        }, ContextCompat.getMainExecutor(context))
        onDispose {
            analysis.clearAnalyzer()
            provider?.unbindAll()
            camera = null
        }
    }

    DisposableEffect(Unit) { onDispose { executor.shutdown() } }

    // The activity handles rotation itself, so tell CameraX explicitly.
    LaunchedEffect(configuration.orientation) {
        previewView.display?.rotation?.let {
            preview.targetRotation = it
            analysis.targetRotation = it
        }
    }

    LaunchedEffect(camera, torchOn) {
        camera?.let { if (it.cameraInfo.hasFlashUnit()) it.cameraControl.enableTorch(torchOn) }
    }

    AndroidView(factory = { previewView }, modifier = modifier)
}
