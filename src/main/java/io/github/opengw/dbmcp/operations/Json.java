package io.github.opengw.dbmcp.operations;

import com.fasterxml.jackson.databind.ObjectMapper;

/** Shared Jackson instance — ObjectMapper is thread-safe. */
class Json {
    static final ObjectMapper MAPPER = new ObjectMapper();
}
