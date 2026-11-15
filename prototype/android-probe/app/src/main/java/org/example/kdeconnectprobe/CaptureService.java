/*
 * SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com>
 *
 * SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL
 */

package org.example.kdeconnectprobe;

import android.Manifest;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.pm.ServiceInfo;
import android.graphics.PixelFormat;
import android.hardware.display.DisplayManager;
import android.hardware.display.VirtualDisplay;
import android.media.AudioAttributes;
import android.media.AudioFormat;
import android.media.AudioPlaybackCaptureConfiguration;
import android.media.AudioRecord;
import android.media.Image;
import android.media.ImageReader;
import android.media.projection.MediaProjection;
import android.media.projection.MediaProjectionManager;
import android.os.Build;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.IBinder;
import android.os.Looper;
import android.util.DisplayMetrics;
import android.view.WindowManager;

import java.util.concurrent.atomic.AtomicInteger;

/** Temporary local measurement of screen frames and capturable playback samples. */
public final class CaptureService extends Service {
    private static final String CHANNEL = "capture_probe";
    private static final int NOTIFICATION = 812;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final AtomicInteger frames = new AtomicInteger();
    private final AtomicInteger audioPeak = new AtomicInteger();
    private final AtomicInteger audioReads = new AtomicInteger();
    private volatile boolean running;
    private volatile String audioStatus = "not started";
    private MediaProjection projection;
    private VirtualDisplay display;
    private ImageReader reader;
    private HandlerThread frameThread;
    private volatile AudioRecord audio;
    private boolean started;
    private final MediaProjection.Callback callback = new MediaProjection.Callback() {
        @Override public void onStop() { stopSelf(); }
    };
    private final Runnable update = new Runnable() {
        @Override public void run() {
            if (!running) return;
            String message = "Frames: " + frames.get() + "; audio reads: " + audioReads.get()
                    + "; peak: " + audioPeak.get() + "; " + audioStatus;
            getSystemService(NotificationManager.class).notify(NOTIFICATION, notification(message));
            main.postDelayed(this, 1000);
        }
    };

    @Override public void onCreate() {
        super.onCreate();
        getSystemService(NotificationManager.class).createNotificationChannel(
                new NotificationChannel(CHANNEL, "Screen capture probe", NotificationManager.IMPORTANCE_LOW));
    }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        if (started) return START_NOT_STICKY;
        if (intent == null) { stopSelf(); return START_NOT_STICKY; }
        Intent consent = intent.getParcelableExtra("consent");
        if (consent == null) { stopSelf(); return START_NOT_STICKY; }
        started = true;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(NOTIFICATION, notification("Starting capture"), ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION);
        } else {
            startForeground(NOTIFICATION, notification("Starting capture"));
        }
        try {
            projection = getSystemService(MediaProjectionManager.class)
                    .getMediaProjection(intent.getIntExtra("result", 0), consent);
            if (projection == null) throw new IllegalStateException("No projection");
            projection.registerCallback(callback, main);
            DisplayMetrics metrics = new DisplayMetrics();
            getSystemService(WindowManager.class).getDefaultDisplay().getRealMetrics(metrics);
            frameThread = new HandlerThread("probe-frames");
            frameThread.start();
            reader = ImageReader.newInstance(metrics.widthPixels, metrics.heightPixels, PixelFormat.RGBA_8888, 2);
            reader.setOnImageAvailableListener(source -> {
                try (Image image = source.acquireLatestImage()) {
                    if (image != null) frames.incrementAndGet();
                } catch (IllegalStateException ignored) {
                    // The reader may be closed during shutdown.
                }
            }, new Handler(frameThread.getLooper()));
            display = projection.createVirtualDisplay("KDE Connect capture probe", metrics.widthPixels,
                    metrics.heightPixels, metrics.densityDpi, DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
                    reader.getSurface(), null, main);
            running = true;
            startAudio();
            main.post(update);
        } catch (RuntimeException e) {
            android.util.Log.e("CaptureProbe", "Capture start failed", e);
            stopSelf();
        }
        return START_NOT_STICKY;
    }

    private void startAudio() {
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            audioStatus = "audio permission denied";
            return;
        }
        final MediaProjection capture = projection;
        new Thread(() -> {
            AudioRecord recorder = null;
            try {
                AudioPlaybackCaptureConfiguration config = new AudioPlaybackCaptureConfiguration.Builder(capture)
                        .addMatchingUsage(AudioAttributes.USAGE_MEDIA)
                        .addMatchingUsage(AudioAttributes.USAGE_GAME)
                        .addMatchingUsage(AudioAttributes.USAGE_UNKNOWN).build();
                int min = AudioRecord.getMinBufferSize(48000, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
                if (min <= 0) throw new IllegalStateException("No compatible audio buffer");
                AudioFormat format = new AudioFormat.Builder().setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                        .setSampleRate(48000).setChannelMask(AudioFormat.CHANNEL_IN_MONO).build();
                recorder = new AudioRecord.Builder().setAudioFormat(format)
                        .setBufferSizeInBytes(Math.max(min, 4096)).setAudioPlaybackCaptureConfig(config).build();
                if (recorder.getState() != AudioRecord.STATE_INITIALIZED) throw new IllegalStateException("Audio not initialized");
                audio = recorder;
                if (!running) return;
                recorder.startRecording();
                audioStatus = "playback capture active";
                short[] samples = new short[2048];
                while (running) {
                    int count = recorder.read(samples, 0, samples.length);
                    if (count < 0) { audioStatus = "audio read failed: " + count; break; }
                    if (count == 0) continue;
                    int peak = 0;
                    for (int i = 0; i < count; i++) peak = Math.max(peak, Math.abs((int) samples[i]));
                    audioPeak.set(peak);
                    audioReads.incrementAndGet();
                }
            } catch (RuntimeException e) {
                audioStatus = "audio unavailable: " + e.getClass().getSimpleName();
                android.util.Log.e("CaptureProbe", "Audio capture failed", e);
            } finally {
                audio = null;
                if (recorder != null) {
                    try { recorder.stop(); } catch (IllegalStateException ignored) { }
                    recorder.release();
                }
            }
        }, "probe-audio").start();
    }

    private Notification notification(String text) {
        Intent open = new Intent(this, MainActivity.class);
        PendingIntent pending = PendingIntent.getActivity(this, 0, open, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        return new Notification.Builder(this, CHANNEL).setSmallIcon(android.R.drawable.ic_menu_view)
                .setContentTitle("Capture probe running").setContentText(text)
                .setStyle(new Notification.BigTextStyle().bigText(text)).setContentIntent(pending).setOngoing(true).build();
    }

    @Override public void onDestroy() {
        running = false;
        main.removeCallbacks(update);
        AudioRecord current = audio;
        if (current != null) {
            try { current.stop(); } catch (IllegalStateException ignored) { }
        }
        if (display != null) display.release();
        if (reader != null) reader.close();
        if (frameThread != null) frameThread.quitSafely();
        if (projection != null) {
            projection.unregisterCallback(callback);
            projection.stop();
        }
        super.onDestroy();
    }

    @Override public IBinder onBind(Intent intent) { return null; }
}
