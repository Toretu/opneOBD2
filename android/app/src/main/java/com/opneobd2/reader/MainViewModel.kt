package com.opneobd2.reader

import android.annotation.SuppressLint
import android.app.Application
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothManager
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.opneobd2.reader.core.Brand
import com.opneobd2.reader.core.Connection
import com.opneobd2.reader.core.MockVehicleConnection
import com.opneobd2.reader.core.ModuleReport
import com.opneobd2.reader.core.SppConnection
import com.opneobd2.reader.core.clearModuleDtcs
import com.opneobd2.reader.core.scanModules
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

enum class Source(val label: String) {
    MOCK("Virtual car (mock)"),
    SPP("Bluetooth Classic (SPP)"),
}

@SuppressLint("MissingPermission")
class MainViewModel(app: Application) : AndroidViewModel(app) {
    var source by mutableStateOf(Source.MOCK)
    var brand by mutableStateOf(Brand.VAG)
    var discover by mutableStateOf(false)
    var activeOnly by mutableStateOf(false)

    var devices by mutableStateOf<List<BluetoothDevice>>(emptyList())
        private set
    var selectedDevice by mutableStateOf<BluetoothDevice?>(null)
    var reports by mutableStateOf<List<ModuleReport>>(emptyList())
        private set
    var status by mutableStateOf("Not connected")
        private set
    var busy by mutableStateOf(false)
        private set
    var connected by mutableStateOf(false)
        private set

    private var connection: Connection? = null

    fun refreshDevices() {
        val adapter = getApplication<Application>()
            .getSystemService(BluetoothManager::class.java)?.adapter
        if (adapter == null) {
            status = "Bluetooth not available"
            return
        }
        devices = try {
            adapter.bondedDevices.toList().sortedBy { it.name ?: it.address }
        } catch (e: SecurityException) {
            status = "Bluetooth permission denied"
            emptyList()
        }
        if (selectedDevice !in devices) selectedDevice = devices.firstOrNull()
    }

    fun connectAndScan() = runTask("Connecting...") {
        disconnectInternal()
        val conn: Connection = when (source) {
            Source.MOCK -> MockVehicleConnection()
            Source.SPP -> SppConnection.open(
                selectedDevice ?: error("Select a paired adapter first"),
            )
        }
        connection = conn
        connected = true
        scan(conn)
    }

    fun rescan() = runTask("Scanning...") { scan(connection ?: error("Not connected")) }

    fun clearModule(requestId: Int) = runTask("Clearing...") {
        val conn = connection ?: error("Not connected")
        val ok = clearModuleDtcs(conn, requestId)
        scan(conn)
        status = if (ok) "Cleared module 0x%03X".format(requestId) else "Module did not confirm clear"
    }

    fun clearAll() = runTask("Clearing all modules...") {
        val conn = connection ?: error("Not connected")
        var cleared = 0
        reports.filter { it.dtcs.isNotEmpty() }.forEachIndexed { i, r ->
            if (clearModuleDtcs(conn, r.requestId, setup = i == 0)) cleared++
        }
        scan(conn)
        status = "Cleared $cleared module(s)"
    }

    fun disconnect() {
        disconnectInternal()
        reports = emptyList()
        status = "Not connected"
    }

    private fun scan(conn: Connection) {
        val result = scanModules(conn, brand, discover, activeOnly) { p -> status = p }
        reports = result
        status = "${result.size} module(s) responded, ${result.sumOf { it.dtcs.size }} code(s)"
    }

    private fun runTask(label: String, block: suspend () -> Unit) {
        if (busy) return
        busy = true
        status = label
        viewModelScope.launch {
            try {
                withContext(Dispatchers.IO) { block() }
            } catch (e: Exception) {
                status = "Error: ${e.message ?: e.javaClass.simpleName}"
                if (connection != null && !connected) disconnectInternal()
            } finally {
                busy = false
            }
        }
    }

    private fun disconnectInternal() {
        connection?.close()
        connection = null
        connected = false
    }

    override fun onCleared() {
        disconnectInternal()
    }
}
