package com.opneobd2.reader.core

private const val LETTERS = "PCBU"

fun bytesToCode(a: Int, b: Int): String {
    val letter = LETTERS[(a shr 6) and 0x03]
    val second = (a shr 4) and 0x03
    val third = a and 0x0F
    val fourth = (b shr 4) and 0x0F
    val fifth = b and 0x0F
    return "%s%d%X%X%X".format(letter, second, third, fourth, fifth)
}

fun codeToBytes(code: String): Pair<Int, Int> {
    val c = code.trim().uppercase()
    require(c.length == 5 && c[0] in LETTERS) { "Invalid DTC code: $c" }
    val second = c[1].digitToInt(16)
    require(second <= 3) { "Invalid DTC code: $c" }
    val a = (LETTERS.indexOf(c[0]) shl 6) or (second shl 4) or c[2].digitToInt(16)
    val b = (c[3].digitToInt(16) shl 4) or c[4].digitToInt(16)
    return a to b
}

private val DTC_DATABASE: Map<String, String> = mapOf(
    "P0300" to "Random / multiple cylinder misfire detected",
    "P0301" to "Cylinder 1 misfire detected",
    "P0302" to "Cylinder 2 misfire detected",
    "P0303" to "Cylinder 3 misfire detected",
    "P0304" to "Cylinder 4 misfire detected",
    "P0305" to "Cylinder 5 misfire detected",
    "P0306" to "Cylinder 6 misfire detected",
    "P0171" to "System too lean (bank 1)",
    "P0172" to "System too rich (bank 1)",
    "P0174" to "System too lean (bank 2)",
    "P0175" to "System too rich (bank 2)",
    "P0130" to "O2 sensor circuit malfunction (bank 1, sensor 1)",
    "P0133" to "O2 sensor circuit slow response (bank 1, sensor 1)",
    "P0135" to "O2 sensor heater circuit malfunction (bank 1, sensor 1)",
    "P0401" to "Exhaust gas recirculation (EGR) flow insufficient",
    "P0402" to "Exhaust gas recirculation (EGR) flow excessive",
    "P0404" to "EGR control circuit range/performance",
    "P0420" to "Catalyst system efficiency below threshold (bank 1)",
    "P0430" to "Catalyst system efficiency below threshold (bank 2)",
    "P0440" to "Evaporative emission (EVAP) system malfunction",
    "P0442" to "EVAP system leak detected (small leak)",
    "P0455" to "EVAP system leak detected (large leak)",
    "P0101" to "Mass air flow (MAF) sensor range/performance",
    "P0113" to "Intake air temperature sensor circuit high",
    "P0118" to "Engine coolant temperature sensor circuit high",
    "P0128" to "Coolant thermostat (below regulating temperature)",
    "P0500" to "Vehicle speed sensor malfunction",
    "P0380" to "Glow plug / heater circuit A malfunction",
    "P0381" to "Glow plug / heater indicator circuit malfunction",
    "P0382" to "Glow plug / heater circuit B malfunction",
    "P0670" to "Glow plug control module circuit malfunction",
    "P0671" to "Cylinder 1 glow plug circuit malfunction",
    "P0672" to "Cylinder 2 glow plug circuit malfunction",
    "P0673" to "Cylinder 3 glow plug circuit malfunction",
    "P0674" to "Cylinder 4 glow plug circuit malfunction",
    "P0675" to "Cylinder 5 glow plug circuit malfunction",
    "P0676" to "Cylinder 6 glow plug circuit malfunction",
    "P0677" to "Cylinder 7 glow plug circuit malfunction",
    "P0678" to "Cylinder 8 glow plug circuit malfunction",
    "P0087" to "Fuel rail / system pressure too low",
    "P0088" to "Fuel rail / system pressure too high",
    "P0234" to "Turbocharger overboost condition",
    "P0299" to "Turbocharger underboost (low boost pressure)",
    "P0471" to "Exhaust pressure sensor range/performance",
    "P2002" to "Diesel particulate filter (DPF) efficiency below threshold",
    "P2015" to "Intake manifold runner / flap position sensor (bank 1)",
    "P242F" to "Diesel particulate filter restricted - ash accumulation",
    "P0562" to "System voltage low",
    "P0563" to "System voltage high",
    "P0700" to "Transmission control system malfunction",
    "P0705" to "Transmission range sensor circuit malfunction",
    "C1011" to "ABS/EBS control unit fault",
    "C1041" to "ABS/EBS wheel speed sensor signal fault",
    "C1200" to "ABS/EBS hydraulic pump / solenoid fault",
    "C1611" to "EBS/ESP control module supply voltage fault",
    "U0100" to "Lost communication with engine control module (ECM)",
    "U0101" to "Lost communication with transmission control module (TCM)",
    "U0121" to "Lost communication with ABS/EBS control module",
)

private val SYSTEM_NAMES = mapOf('P' to "Powertrain", 'C' to "Chassis", 'B' to "Body", 'U' to "Network")

private val P_SUBSYSTEMS = mapOf(
    '0' to "fuel and air metering",
    '1' to "fuel and air metering",
    '2' to "fuel and air metering (injector circuit)",
    '3' to "ignition system or misfire",
    '4' to "auxiliary emission controls",
    '5' to "vehicle speed, idle control and auxiliary inputs",
    '6' to "computer output circuit / auxiliary outputs",
    '7' to "transmission",
    '8' to "transmission",
    '9' to "transmission",
    'A' to "hybrid propulsion",
    'B' to "hybrid propulsion",
    'C' to "hybrid propulsion",
)

private fun isManufacturerSpecific(code: String): Boolean {
    val letter = code[0]
    val second = code[1]
    if (letter == 'P') {
        if (second == '1') return true
        if (second == '3') return code[2] in "0123"
        return false
    }
    return second == '1'
}

fun describe(code: String): String {
    val c = code.trim().uppercase()
    DTC_DATABASE[c]?.let { return it }

    val system = SYSTEM_NAMES[c.firstOrNull()] ?: "Unknown"
    val scope = if (isManufacturerSpecific(c)) "manufacturer-specific" else "generic/standard"
    if (c[0] == 'P' && c.length >= 3) {
        P_SUBSYSTEMS[c[2]]?.let { return "$system - $it ($scope code)" }
    }
    return "$system ($scope trouble code)"
}
