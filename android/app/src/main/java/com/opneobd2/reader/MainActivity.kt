package com.opneobd2.reader

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.Checkbox
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import com.opneobd2.reader.core.Brand
import com.opneobd2.reader.core.ModuleReport
import com.opneobd2.reader.core.formatReport

class MainActivity : ComponentActivity() {
    private val vm: MainViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme { Screen(vm) }
        }
    }
}

private fun hasBtPermission(ctx: Context): Boolean =
    Build.VERSION.SDK_INT < Build.VERSION_CODES.S ||
        ContextCompat.checkSelfPermission(ctx, Manifest.permission.BLUETOOTH_CONNECT) ==
        PackageManager.PERMISSION_GRANTED

@Composable
private fun <T> Picker(
    label: String,
    value: String,
    options: List<T>,
    optionLabel: (T) -> String,
    enabled: Boolean = true,
    onSelect: (T) -> Unit,
) {
    var open by remember { mutableStateOf(false) }
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
        Text("$label:", modifier = Modifier.padding(end = 8.dp))
        OutlinedButton(onClick = { open = true }, enabled = enabled) { Text(value) }
        DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            options.forEach {
                DropdownMenuItem(
                    text = { Text(optionLabel(it)) },
                    onClick = { onSelect(it); open = false },
                )
            }
        }
    }
}

@Composable
private fun Screen(vm: MainViewModel) {
    val ctx = androidx.compose.ui.platform.LocalContext.current
    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { vm.refreshDevices() }

    fun pickSource(s: Source) {
        vm.source = s
        if (s == Source.SPP) {
            if (hasBtPermission(ctx)) vm.refreshDevices()
            else permissionLauncher.launch(Manifest.permission.BLUETOOTH_CONNECT)
        }
    }

    LazyColumn(
        modifier = Modifier.statusBarsPadding().padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        item {
            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text("OBD2 Reader", style = MaterialTheme.typography.headlineSmall)
                Picker("Source", vm.source.label, Source.entries, { it.label },
                    enabled = !vm.busy && !vm.connected) { pickSource(it) }
                if (vm.source == Source.SPP) {
                    Picker(
                        "Adapter",
                        vm.selectedDevice?.let { dev ->
                            if (hasBtPermission(ctx)) dev.name ?: dev.address else dev.address
                        } ?: "none (pair in Android settings)",
                        vm.devices,
                        { dev -> (if (hasBtPermission(ctx)) dev.name else null) ?: dev.address },
                        enabled = !vm.busy && !vm.connected,
                    ) { vm.selectedDevice = it }
                }
                Picker("Vehicle", vm.brand.label, Brand.entries.toList(), { it.label },
                    enabled = !vm.busy) { vm.brand = it }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Checkbox(vm.discover, { vm.discover = it })
                    Text("Discover modules")
                    Checkbox(vm.activeOnly, { vm.activeOnly = it })
                    Text("Active only")
                }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (!vm.connected) {
                        Button(onClick = { vm.connectAndScan() }, enabled = !vm.busy) {
                            Text("Connect & scan")
                        }
                    } else {
                        Button(onClick = { vm.rescan() }, enabled = !vm.busy) { Text("Rescan") }
                        OutlinedButton(onClick = { vm.disconnect() }, enabled = !vm.busy) {
                            Text("Disconnect")
                        }
                    }
                }
                if (vm.connected) {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        OutlinedButton(
                            onClick = { vm.clearAll() },
                            enabled = !vm.busy && vm.reports.any { it.dtcs.isNotEmpty() },
                        ) { Text("Clear all") }
                        OutlinedButton(
                            onClick = { shareReport(ctx, vm.reports) },
                            enabled = vm.reports.isNotEmpty(),
                        ) { Text("Export") }
                    }
                }
                Text(vm.status, style = MaterialTheme.typography.bodyMedium)
            }
        }
        items(vm.reports, key = { it.requestId }) { report ->
            ModuleCard(report, busy = vm.busy) { vm.clearModule(report.requestId) }
        }
    }
}

@Composable
private fun ModuleCard(report: ModuleReport, busy: Boolean, onClear: () -> Unit) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    "${report.name} (${report.dtcs.size})",
                    fontWeight = FontWeight.Bold,
                    modifier = Modifier.weight(1f),
                )
                if (report.dtcs.isNotEmpty()) {
                    TextButton(onClick = onClear, enabled = !busy) { Text("Clear") }
                }
            }
            if (report.dtcs.isEmpty()) Text("No codes", color = Color(0xFF2E7D32))
            report.dtcs.forEach { d ->
                val color = if (d.active) Color(0xFFC62828) else Color.Gray
                Column {
                    Text(d.code, color = color, fontWeight = FontWeight.Bold)
                    Text(d.description, color = color)
                    Text(d.statusText, style = MaterialTheme.typography.bodySmall, color = Color.Gray)
                }
            }
        }
    }
}

private fun shareReport(ctx: Context, reports: List<ModuleReport>) {
    val send = Intent(Intent.ACTION_SEND).apply {
        type = "text/plain"
        putExtra(Intent.EXTRA_TEXT, formatReport(reports))
    }
    ctx.startActivity(Intent.createChooser(send, "Export report"))
}
