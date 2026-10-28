package com.opneobd2.reader.core

class MockFault(val code: String, val failureType: Int = 0, val status: Int = STATUS_ACTIVE)

class MockModule(val requestId: Int, val name: String, val faults: MutableList<MockFault>)

const val STATUS_ACTIVE = 0x88
const val STATUS_PASSIVE = 0x50

fun defaultMockVehicle(): List<MockModule> = listOf(
    MockModule(0x7E0, "Engine", mutableListOf(
        MockFault("P0671"), MockFault("P0380"), MockFault("P2002", status = STATUS_PASSIVE),
    )),
    MockModule(0x713, "ABS / ESP brakes", mutableListOf(MockFault("C1041"))),
    MockModule(0x714, "Instrument cluster", mutableListOf(MockFault("U0100", status = STATUS_PASSIVE))),
    MockModule(0x715, "Airbag / SRS", mutableListOf()),
)

/** Simulated UDS-aware ELM327 for hardware-free demos. */
class MockVehicleConnection(private val modules: List<MockModule> = defaultMockVehicle()) : Connection {
    private var response = ""
    private var requestId = 0

    override fun write(data: String) {
        val cmd = data.trim().uppercase()
        response = when {
            cmd.startsWith("ATSH") -> {
                requestId = cmd.removePrefix("ATSH").toIntOrNull(16) ?: 0
                "OK"
            }
            cmd.startsWith("AT") -> "OK"
            cmd == "1902FF" -> readDtcs()
            cmd == "14FFFFFF" -> clear()
            else -> "NO DATA"
        }
    }

    override fun readUntilPrompt(): String = response.also { response = "" }

    override fun close() {}

    private fun module() = modules.firstOrNull { it.requestId == requestId }

    private fun clear(): String {
        val m = module() ?: return "NO DATA"
        m.faults.clear()
        return "%03X0154".format(responseIdFor(requestId))
    }

    private fun readDtcs(): String {
        val m = module() ?: return "NO DATA"
        val payload = mutableListOf(0x59, 0x02, 0xFF)
        for (f in m.faults) {
            val (a, b) = codeToBytes(f.code)
            payload += listOf(a, b, f.failureType, f.status)
        }
        val id = "%03X".format(responseIdFor(requestId))
        if (payload.size <= 7) {
            return id + "%02X".format(payload.size) + payload.joinToString("") { "%02X".format(it) }
        }
        val lines = mutableListOf<String>()
        val len = payload.size
        lines += id + "%02X%02X".format(0x10 or (len shr 8), len and 0xFF) +
            payload.take(6).joinToString("") { "%02X".format(it) }
        var seq = 1
        for (chunk in payload.drop(6).chunked(7)) {
            lines += id + "%02X".format(0x20 or (seq and 0x0F)) +
                chunk.joinToString("") { "%02X".format(it) }
            seq++
        }
        return lines.joinToString("\r")
    }
}
