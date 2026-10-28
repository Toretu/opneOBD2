package com.opneobd2.reader.core

import android.annotation.SuppressLint
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothSocket
import java.io.InputStream
import java.io.OutputStream
import java.util.UUID

private val SPP_UUID: UUID = UUID.fromString("00001101-0000-1000-8000-00805F9B34FB")
private const val READ_TIMEOUT_MS = 5000L

/** Bluetooth Classic (SPP/RFCOMM) ELM327 transport. Caller must hold BLUETOOTH_CONNECT. */
@SuppressLint("MissingPermission")
class SppConnection private constructor(
    private val socket: BluetoothSocket,
    private val input: InputStream,
    private val output: OutputStream,
) : Connection {

    override fun write(data: String) {
        output.write((data + "\r").toByteArray(Charsets.US_ASCII))
        output.flush()
    }

    override fun readUntilPrompt(): String {
        val sb = StringBuilder()
        val deadline = System.currentTimeMillis() + READ_TIMEOUT_MS
        while (System.currentTimeMillis() < deadline) {
            if (input.available() == 0) {
                Thread.sleep(10)
                continue
            }
            val b = input.read()
            if (b < 0 || b == '>'.code) break
            sb.append(b.toChar())
        }
        return sb.toString()
    }

    override fun close() {
        runCatching { socket.close() }
    }

    companion object {
        @SuppressLint("MissingPermission")
        fun open(device: BluetoothDevice): SppConnection {
            BluetoothAdapter.getDefaultAdapter()?.cancelDiscovery()
            val socket = try {
                device.createRfcommSocketToServiceRecord(SPP_UUID).also { it.connect() }
            } catch (e: Exception) {
                // Some clones don't advertise SPP; fall back to RFCOMM channel 1.
                @Suppress("DiscouragedPrivateApi")
                val fallback = device.javaClass.getMethod("createRfcommSocket", Int::class.javaPrimitiveType)
                    .invoke(device, 1) as BluetoothSocket
                fallback.connect()
                fallback
            }
            return SppConnection(socket, socket.inputStream, socket.outputStream)
        }
    }
}
