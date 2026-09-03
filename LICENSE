# Licensing

**This document records the project's reasoning about third-party licences. It
is not legal advice. It is written by the maintainer, not by counsel, and where
a question is genuinely unresolved it says so rather than asserting an answer.**

Downstream consumers with specific compliance obligations should form their own
view and are encouraged to open an issue if they believe anything here is wrong.

---

## 1. Project licence

GatewayDB-MCP source code is licensed under the Apache License, Version 2.0. See
`LICENSE`.

Contributions are accepted inbound under the same terms. See `CONTRIBUTING.md`.

## 2. What the distributed artifact contains

The project publishes a shaded uber-JAR built with `maven-shade-plugin`. That
artifact contains this project's code **and** the bytecode of its dependencies.
The dependencies retain their own licences; shading does not relicense them.

This distinction matters and has not previously been stated clearly. "This
project is licensed under Apache 2.0" is a statement about this project's
source. It is not a statement about every class inside the shaded JAR.

Relocation: HikariCP and Jackson are relocated under
`io.github.opengw.dbmcp.shaded.*`. **JDBC drivers are not relocated**; their
bytecode is included unmodified under its original package names.

## 3. Dependency inventory

### Runtime, bundled in the default build

| Dependency | Version | Licence | Source of determination |
|---|---|---|---|
| HikariCP | 5.x | Apache-2.0 | artifact POM |
| Jackson | 2.15 | Apache-2.0 | artifact POM |
| PostgreSQL JDBC | 42.6.0 | BSD-2-Clause | artifact POM |
| Microsoft JDBC (SQL Server) | 12.4.1.jre11 | MIT | artifact POM |
| MySQL Connector/J | 8.0.33 | GPL-2.0 with the Universal FOSS Exception v1.0 | artifact POM and upstream `LICENSE` |

### Optional, not bundled by default

| Dependency | Version | Licence | Build profile |
|---|---|---|---|
| MariaDB Connector/J | 3.3.3 | LGPL-2.1 | `-Pmariadb` |
| Oracle JDBC (`ojdbc11`) | operator-supplied | Oracle proprietary terms | `-Poracle` |

MariaDB Connector/J brings `com.github.waffle:waffle-jna` (MIT) transitively,
which in turn brings `net.java.dev.jna:jna` and `jna-platform` 5.13.0.

## 4. Analysis

### 4.1 MySQL Connector/J and the Universal FOSS Exception

MySQL Connector/J 8.0.33 is licensed under GPLv2 with the Universal FOSS
Exception, version 1.0.

The exception is **narrower than it is often assumed to be**. Reading the text
at <http://oss.oracle.com/licenses/universal-foss-exception>, it grants
permission concerning *Interfaces* — constants, function signatures, data
structures and other invocation methods — so that software under an OSI-approved
or FSF-free licence ("Other FOSS") can interoperate with the GPL'd software
without the Other FOSS being forced under the GPL. It states that this includes
statically or dynamically linking the Software together with Other FOSS.

It also states explicitly that nothing in the permission grants any right to
distribute any portion of the Software on terms other than the Software Licence.

Two consequences for this project:

1. **This project's own code remains Apache 2.0** notwithstanding that it links
   against and ships alongside Connector/J. That is what the exception is for,
   and Apache 2.0 is OSI-approved, so it qualifies as Other FOSS.
2. **Connector/J's bytecode inside the shaded JAR remains under GPLv2 with the
   exception.** It does not become Apache 2.0. The shaded artifact is therefore
   a combination of differently licensed components.

The obligation that follows is disclosure, not removal: recipients must be told
what is inside the artifact and under what terms, and the GPL's source
availability requirement applies to the Connector/J portion. Oracle publishes
that source.

### 4.2 MariaDB Connector/J and LGPL-2.1

MariaDB Connector/J 3.3.3 is licensed under LGPL-2.1. There is no equivalent of
Oracle's Universal FOSS Exception.

LGPL-2.1 §6 permits combining the library with a work under other terms, subject
to conditions intended to preserve the recipient's ability to modify the library
and relink. Section 6(a) contemplates supplying the complete corresponding
source for the library together with whatever is needed to relink; §6(b)
contemplates a shared-library mechanism.

Whether a shaded uber-JAR satisfies those conditions is **genuinely unsettled**,
and this project does not attempt to resolve it. Two observations bear on it:

- The driver's bytecode is **not relocated or modified** by the shade plugin. It
  is co-located in a single archive under its original package names. That is
  closer to aggregation than to the kind of modification LGPL's relinking
  provision is principally aimed at.
- Nevertheless, a recipient wishing to substitute a different version of the
  driver cannot simply replace a JAR on the classpath, because the classes are
  inside the project's own artifact. That is the practical difficulty.

Rather than take a position on an unsettled question in a project that other
people build on, MariaDB support is provided through an optional build profile.
The default artifact contains no LGPL code, and operators who need MariaDB
build with `-Pmariadb` and are the party making the distribution decision.

### 4.3 JNA dual licence

`net.java.dev.jna:jna` and `jna-platform` 5.13.0 are dual-licensed:
LGPL-2.1-or-later **or** Apache-2.0, at the recipient's election.

A dual licence is only resolved when the recipient chooses. **This project elects
Apache-2.0** for both artifacts. This election is recorded here so that
downstream consumers can see it rather than having to infer it.

JNA reaches this project only transitively through MariaDB Connector/J, so it is
absent from the default build.

### 4.4 Oracle JDBC

`ojdbc11` is distributed under Oracle's own terms, not an OSI-approved licence.
It is never bundled. Operators install it into their local repository and build
with `-Poracle`, accepting Oracle's terms directly.

## 5. Posture

The rule this project applies:

> A dependency is bundled in the default artifact when its licence permits
> redistribution inside a combined work without imposing conditions on the
> recipient beyond attribution and notice. A dependency whose licence imposes
> conditions on the recipient's ability to modify or replace it, or whose
> compatibility with distribution inside a shaded archive is unsettled, is
> provided through an optional build profile instead, so that the operator makes
> the distribution decision.

Under that rule:

- Apache-2.0, BSD, MIT dependencies: bundled.
- MySQL Connector/J: bundled, on the basis of the Universal FOSS Exception,
  with its GPLv2 status disclosed in `NOTICE`. **[DECISION REQUIRED — see §7]**
- MariaDB Connector/J: optional profile.
- Oracle JDBC: optional profile, operator-supplied.

## 6. Obligations this creates

1. **A `NOTICE` file** listing every bundled dependency, its licence, and where
   its source can be obtained. Apache 2.0 §4(d) requires propagating notices;
   GPLv2 requires source availability for the Connector/J portion.
2. **Accurate description of the artifact.** The README and release notes should
   not describe the shaded JAR as simply "Apache 2.0" without qualification. The
   project's source is Apache 2.0; the artifact is a combination.
3. **Re-verification on dependency upgrade.** Licences change between versions.
   A dependency bump is a licence review.

## 7. Open decisions

**D1. Whether MySQL Connector/J should remain bundled.**

Arguments for: the Universal FOSS Exception exists precisely to permit this,
Apache 2.0 qualifies as Other FOSS, and MySQL is the primary demonstration
database. Removing it degrades the default experience substantially.

Arguments against: it places GPLv2 bytecode inside an artifact most users will
assume is uniformly Apache 2.0, and the rule in §5 is cleaner if applied
uniformly to all copyleft.

A third option is to bundle nothing and make every driver a profile, which is
the most defensible position and the least convenient one.

**This decision has not been made.** Until it is, §5 records current practice
rather than a settled policy.

**D2. Whether to seek a professional review.**

The project is publicly archived with a DOI and is cited in academic work, so
the accuracy of these statements has consequences beyond the repository. A
review by someone qualified would be proportionate, particularly on §4.1 and
§4.2.

## 8. Uncertainty register

- The MySQL Connector/J POM declares "The GNU General Public License, v2 with
  Universal FOSS Exception, v1.0". The upstream `LICENSE` file text has been read
  and is consistent. No professional review has been obtained.
- MariaDB Connector/J's POM declares `LGPL-2.1`; the upstream README describes
  it as LGPL-2.1-or-later. The difference may matter and has not been resolved.
- Whether shading satisfies LGPL-2.1 §6 is unsettled and this project takes no
  position. The optional-profile arrangement is chosen to avoid needing one.
- Transitive dependency licences were determined from artifact POMs. POM
  metadata is occasionally inaccurate or incomplete relative to the actual
  `LICENSE` file in the artifact.

## 9. Reporting a problem

If you believe any statement here is wrong, please open an issue. Licensing
errors in a published artifact are worth correcting promptly and publicly.
