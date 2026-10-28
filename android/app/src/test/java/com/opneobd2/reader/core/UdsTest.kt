package com.opneobd2.reader.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class UdsTest {
    @Test
    fun codeRoundTrip() {
        val (a, b) = codeToBytes("P0380")
        assertEquals("P0380", bytesToCode(a, b))
    }

    @Test
    fun mockFullScan() {
        val reports = scanModules(MockVehicleConnection())
        val engine = reports.first { it.requestId == 0x7E0 }
        assertEquals(listOf("P0671", "P0380", "P2002"), engine.dtcs.map { it.code })
        assertTrue(engine.dtcs[0].active)
        assertTrue(!engine.dtcs[2].active)
        assertTrue(reports.none { it.requestId == 0x710 })
    }

    @Test
    fun clearModule() {
        val conn = MockVehicleConnection()
        assertTrue(clearModuleDtcs(conn, 0x713))
        val abs = scanModules(conn).first { it.requestId == 0x713 }
        assertTrue(abs.dtcs.isEmpty())
    }
}
