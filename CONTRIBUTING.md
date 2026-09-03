# Contributing to GatewayDB-MCP

Thank you for your interest.

## Licensing of contributions

This project is licensed under the Apache License 2.0. By submitting a pull
request you agree that your contribution is licensed under the same terms
(inbound = outbound).

If your change adds a third-party dependency, state its licence in the pull
request description and update [`docs/LICENSING.md`](docs/LICENSING.md).

Not all copyleft licences are treated alike. A dependency may be bundled in the
default shaded artifact when its licence (including any exception attached to
it) permits redistribution inside a combined Apache-2.0 work without imposing
conditions on the recipient beyond attribution and notice — MySQL Connector/J
under GPLv2 with the Universal FOSS Exception is the existing example. See
`docs/LICENSING.md` for the full posture.

A dependency whose licence does not clearly permit that arrangement, or whose
compatibility with shading is unsettled (for example LGPL-2.1 without an
equivalent exception), must not be bundled by default. Declare it `provided`
scope, expose it through an optional Maven profile (see `-Pmariadb` /
`-Poracle` in `pom.xml`), and document the build step for operators. Do not
treat the Oracle JDBC profile as the only template: MariaDB uses the same
mechanism for a different licence reason.

## How to contribute

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes
4. Push to the branch
5. Open a pull request

Please open an issue first for anything substantial, so we can agree on the
approach before you spend time on it.

## Development setup

- Java 11 (the project targets `maven.compiler.release=11`; the sidecar image
  runs a JRE 11 base, so Java 12+ APIs will compile locally and fail at runtime)
- Maven 3.8+
- Docker, for sidecar and integration testing

```bash
mvn clean package
```

## Tests

Unit and integration tests are under `src/test/java`, using H2 in-memory for
database-backed cases. The CI gate requires 70% line coverage.

```bash
mvn test
```

For changes that add or alter database support, please also validate against a
running instance rather than configuration resolution alone. The evaluation
harness in `bench/` runs the bridge against real database containers and
compares the generated tool manifests; see `bench/README.md`.

## Changes touching security

`QueryValidator`, the allowlist, credential handling and the layered access
controls are described in the README's security model and in `SECURITY.md`. If
your change affects any of them, say so in the pull request and note whether the
documentation needs updating.

Do not report security vulnerabilities in a public pull request. See
`SECURITY.md`.
