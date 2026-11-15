/*
 * SPDX-FileCopyrightText: 2026 Dmitriy Volkov <dennshield@duck.com>
 *
 * SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL
 */

package org.example.kdeconnectprobe;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.media.projection.MediaProjectionManager;
import android.os.Bundle;
import android.os.Build;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

/** Local diagnostic only: no networking or stored media. */
public final class MainActivity extends Activity {
    private static final int AUDIO_REQUEST = 1;
    private static final int PROJECTION_REQUEST = 2;
    private TextView status;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        int pad = (int) (16 * getResources().getDisplayMetrics().density);
        layout.setPadding(pad, pad, pad, pad);
        status = new TextView(this);
        status.setText("Local capture probe. No image or audio leaves this phone. " +
                "Grant audio permission to measure eligible playback; screen capture still works without it. " +
                "Statistics appear in the foreground notification. Stop the session when finished.");
        layout.addView(status);
        Button start = new Button(this);
        start.setText("Start capture probe");
        start.setOnClickListener(v -> {
            if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED
                    || (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)) {
                requestPermissions(Build.VERSION.SDK_INT >= 33
                        ? new String[]{Manifest.permission.RECORD_AUDIO, Manifest.permission.POST_NOTIFICATIONS}
                        : new String[]{Manifest.permission.RECORD_AUDIO}, AUDIO_REQUEST);
            } else {
                requestProjection();
            }
        });
        layout.addView(start);
        Button stop = new Button(this);
        stop.setText("Stop capture probe");
        stop.setOnClickListener(v -> stopService(new Intent(this, CaptureService.class)));
        layout.addView(stop);
        setContentView(layout, new ViewGroup.LayoutParams(-1, -1));
    }

    @Override public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(requestCode, permissions, results);
        if (requestCode == AUDIO_REQUEST) requestProjection();
    }

    private void requestProjection() {
        MediaProjectionManager manager = getSystemService(MediaProjectionManager.class);
        startActivityForResult(manager.createScreenCaptureIntent(), PROJECTION_REQUEST);
    }

    @Override protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        if (request != PROJECTION_REQUEST) return;
        if (result != RESULT_OK || data == null) {
            status.setText("Screen capture permission was not granted.");
            return;
        }
        Intent service = new Intent(this, CaptureService.class);
        service.putExtra("result", result);
        service.putExtra("consent", data);
        try {
            startForegroundService(service);
            status.setText("Starting. Check the capture notification for status. Use Stop to end the probe.");
        } catch (RuntimeException e) {
            status.setText("Could not start capture: " + e.getClass().getSimpleName());
        }
    }
}
