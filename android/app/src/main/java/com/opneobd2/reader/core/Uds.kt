package com.opneobd2.reader.core

enum class Brand(val label: String) {
    VAG("VW / Audi / SEAT / Skoda"),
    KIA("Kia / Hyundai EV"),
}

private val STATUS_FLAGS = listOf(
    0x01 to "test failed",
    0x02 to "failed this cycle",
    0x04 to "pending",
    0x08 to "confirmed",
    0x10 to "not completed since clear",
    0x20 to "failed since clear",
    0x40 to "not completed this cycle",
    0x80 to "warning light on",
)

val VAG_MODULES: Map<Int, String> = linkedMapOf(
    0x7E0 to "Engine",
    0x7E1 to "Transmission",
    0x710 to "Gateway",
    0x712 to "Steering assist",
    0x713 to "ABS / ESP brakes",
    0x714 to "Instrument cluster",
    0x715 to "Airbag / SRS",
    0x716 to "Steering column electronics",
    0x70A to "Door electronics, driver",
    0x70C to "Door electronics, passenger",
    0x70E to "Central electrics",
    0x717 to "Comfort / convenience",
)

val KIA_MODULES: Map<Int, String> = linkedMapOf(
    0x7E2 to "Vehicle motor control (VMCU)",
    0x7E4 to "Battery management (BMS)",
    0x794 to "On-board charger (OBC)",
    0x7C5 to "DC-DC converter (LDC)",
    0x7C6 to "Instrument cluster",
    0x7D6 to "Tyre pressure (TPMS)",
)

val KIA_DISCOVER_RANGE: IntRange = 0x780..0x7E7

data class ModuleDtc(
    val code: String,
    val failureType: Int,
    val status: Int,
    val description: String,
) {
    val active: Boolean get() = (status and 0x08) != 0 || (status and 0x80) != 0

    val statusText: String
        get() = STATUS_FLAGS.filter { (status and it.first) != 0 }
            .joinToString(", ") { it.second }
            .ifEmpty { "no flags" }
}

data class ModuleReport(val requestId: Int, val name: String, val dtcs: List<ModuleDtc>)

fun responseIdFor(requestId: Int): Int =
    if (requestId >= 0x740) requestId + 8 else requestId + 0x6A

fun decodeDtcRecords(message: ByteArray): List<ModuleDtc> {
    if (message.size < 3 || message[0] != 0x59.toByte() || message[1] != 0x02.toByte()) return emptyList()
    val out = mutableListOf<ModuleDtc>()
    var i = 3
    while (i + 3 < message.size) {
        val b1 = message[i].toInt() and 0xFF
        val b2 = message[i + 1].toInt() and 0xFF
        val b3 = message[i + 2].toInt() and 0xFF
        val status = message[i + 3].toInt() and 0xFF
        i += 4
        if (b1 == 0 && b2 == 0 && b3 == 0) continue
        val code = bytesToCode(b1, b2)
        out += ModuleDtc(code, b3, status, describe(code))
    }
    return out
}

private fun hexToBytes(s: String): ByteArray? {
    if (s.isEmpty() || s.length % 2 != 0 || !s.all { it.isDigit() || it.uppercaseChar() in 'A'..'F' }) return null
    return ByteArray(s.length / 2) { s.substring(it * 2, it * 2 + 2).toInt(16).toByte() }
}

/** Reassembles the last positive `59 02` ISO-TP message from an ELM reply (headers on). */
fun reassembleIsotp(raw: String, responseId: Int): ByteArray? {
    val hexId = "%03X".format(responseId)
    val messages = mutableListOf<ByteArray>()
    var current = mutableListOf<Byte>()
    var expected = 0

    for (rawLine in raw.replace("\r", "\n").split("\n")) {
        val line = rawLine.trim().replace(" ", "").uppercase()
        if (!line.startsWith(hexId)) continue
        var frameHex = line.substring(hexId.length)
        if (frameHex.length % 2 != 0) frameHex = frameHex.dropLast(1)
        val frame = hexToBytes(frameHex) ?: continue
        val pci = (frame[0].toInt() and 0xFF) shr 4
        val low = frame[0].toInt() and 0x0F
        when (pci) {
            0 -> {
                val end = minOf(frame.size, 1 + low)
                messages += frame.copyOfRange(1, end)
                current = mutableListOf(); expected = 0
            }
            1 -> {
                if (frame.size < 2) continue
                expected = (low shl 8) or (frame[1].toInt() and 0xFF)
                current = frame.copyOfRange(2, frame.size).toMutableList()
            }
            2 -> {
                current.addAll(frame.copyOfRange(1, frame.size).toList())
                if (expected > 0 && current.size >= expected) {
                    messages += current.take(expected).toByteArray()
                    current = mutableListOf(); expected = 0
                }
            }
        }
    }
    if (expected > 0 && current.isNotEmpty()) messages += current.take(expected).toByteArray()

    return messages.asReversed().firstOrNull {
        it.size >= 3 && it[0] == 0x59.toByte() && it[1] == 0x02.toByte()
    }
}

private fun setup(conn: Connection) {
    for (cmd in listOf("ATZ", "ATE0", "ATL0", "ATS0", "ATH1", "ATSP6", "ATCAF1")) {
        conn.write(cmd); conn.readUntilPrompt()
    }
    conn.write("0100"); conn.readUntilPrompt()
}

private fun selectModule(conn: Connection, requestId: Int) {
    conn.write("ATSH%03X".format(requestId)); conn.readUntilPrompt()
    conn.write("ATCRA%03X".format(responseIdFor(requestId))); conn.readUntilPrompt()
}

/** Returns null when the module is silent. */
private fun readModule(conn: Connection, requestId: Int): List<ModuleDtc>? {
    selectModule(conn, requestId)
    conn.write("1902FF")
    val raw = conn.readUntilPrompt()
    val compact = raw.replace("\r", "").replace(" ", "").uppercase()
    if ("5902" !in compact && "7F19" !in compact) return null
    val message = reassembleIsotp(raw, responseIdFor(requestId)) ?: return emptyList()
    return decodeDtcRecords(message)
}

fun scanModules(
    conn: Connection,
    brand: Brand = Brand.VAG,
    discover: Boolean = false,
    activeOnly: Boolean = false,
    onProgress: (String) -> Unit = {},
): List<ModuleReport> {
    setup(conn)
    val names = if (brand == Brand.KIA) KIA_MODULES else VAG_MODULES
    val candidates: List<Int> = when {
        brand == Brand.KIA -> if (discover) KIA_DISCOVER_RANGE.toList() else KIA_MODULES.keys.toList()
        discover -> (0x700 until 0x740).toList() + (0x7E0 until 0x7E8).toList()
        else -> VAG_MODULES.keys.toList()
    }

    val reports = mutableListOf<ModuleReport>()
    for (id in candidates) {
        onProgress("Probing 0x%03X".format(id))
        var dtcs = readModule(conn, id) ?: continue
        if (activeOnly) dtcs = dtcs.filter { it.active }
        reports += ModuleReport(id, names[id] ?: "Module 0x%03X".format(id), dtcs)
    }
    return reports
}

/** UDS ClearDiagnosticInformation (0x14, all groups). True on positive 0x54. */
fun clearModuleDtcs(conn: Connection, requestId: Int, setup: Boolean = true): Boolean {
    if (setup) setup(conn)
    selectModule(conn, requestId)
    conn.write("14FFFFFF")
    val raw = conn.readUntilPrompt()
    return "54" in raw.replace("\r", "").replace(" ", "").uppercase()
}

fun formatReport(reports: List<ModuleReport>): String = buildString {
    for (r in reports) {
        appendLine("%s (0x%03X): %d code(s)".format(r.name, r.requestId, r.dtcs.size))
        for (d in r.dtcs) {
            appendLine("  %s %s  [%s]".format(if (d.active) "*" else " ", d.code, d.statusText))
            appendLine("      ${d.description}")
        }
    }
}
