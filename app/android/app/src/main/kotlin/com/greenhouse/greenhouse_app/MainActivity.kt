package com.greenhouse.greenhouse_app

import android.content.Context
import android.net.wifi.WifiManager
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.embedding.android.FlutterActivity
import io.flutter.plugin.common.MethodChannel

// mDNS discovery (pairing_screen.dart's "Find my greenhouse") receives nothing
// on many devices without this: Android is free to filter out multicast
// packets — including mDNS — to save power unless the app explicitly holds a
// WifiManager.MulticastLock. Neither Flutter nor the multicast_dns package
// acquire one, so this channel is the only thing that does.
class MainActivity : FlutterActivity() {
    private val CHANNEL = "greenhouse/multicast"
    private var multicastLock: WifiManager.MulticastLock? = null

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, CHANNEL).setMethodCallHandler { call, result ->
            when (call.method) {
                "acquire" -> {
                    if (multicastLock == null) {
                        val wifi = applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
                        multicastLock = wifi.createMulticastLock("greenhouseMdnsDiscovery").apply {
                            setReferenceCounted(true)
                        }
                    }
                    multicastLock?.acquire()
                    result.success(null)
                }
                "release" -> {
                    // Guard against a stray release with no matching acquire (e.g. a
                    // hot-restarted Dart isolate re-running dispose): isHeld avoids
                    // WifiManager throwing on an unbalanced release call.
                    if (multicastLock?.isHeld == true) multicastLock?.release()
                    result.success(null)
                }
                else -> result.notImplemented()
            }
        }
    }
}
