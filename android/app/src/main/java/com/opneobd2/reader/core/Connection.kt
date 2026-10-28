package com.opneobd2.reader.core

/** Minimal ELM327 transport; mirrors the Python `Connection` protocol. */
interface Connection {
    fun write(data: String)
    fun readUntilPrompt(): String
    fun close()
}
