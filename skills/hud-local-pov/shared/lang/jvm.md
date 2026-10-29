# Language pack: JVM (Java, Kotlin)

Read in X1 when the service runs on the JVM, and follow it wherever SKILL.md says "the
language pack". Every pack uses the same headings; `signals.md` holds the cross-SDK tables.

**Install skill:** `install-hud-sdk-jvm` (a `-javaagent`). **Version gates** (`install-hud-sdk-jvm`):
`io.hud:hud-api` 0.1.0 needs agent v0.2.2 or newer, JDBC outbound landed in v0.2.9, http4k
and Kotlin (coroutines included) in v0.3.0.
- The agent enforces which runtimes and frameworks it supports, at run time. Read the
  support policy at the agent's release tag (`install-hud-sdk-jvm` says where), not in a
  stale clone.
- **One install per JVM process**, whatever mix of Java and Kotlin it holds. Scala, Groovy
  and Clojure are untested: `install-hud-sdk-jvm` stops on a service written substantially in
  one of them, and so does this skill.

## Install check (when Hud maps none of the app's code)

`install-hud-sdk` confirmed the app's code is mapped; use this only to diagnose a run where
the PR's changed functions get no data in Hud after X3's coverage sweep.

1. **Primary: ask Hud.** Count the service's functions by file through the Hud MCP. App
   classes must be there:

   ```sql
   SELECT multiIf(f.file LIKE '%<app package path>/%', 'app', f.third_party != '', 'third-party', 'other') AS kind,
          count() AS functions
   FROM Functions f WHERE arrayExists(s -> s.2 = 'SERVICE', f.services) GROUP BY kind LIMIT 10
   ```

   Use the app's package path, e.g. `%io/spring/%`.
2. **Local fallback: the agent's stdout.** It prints the same four success lines as the
   other SDKs (`Hud session started`, `First code mapping sent successfully`, `First
   invocation sent successfully`, `Your service is sending data successfully`), and where
   they stop is the diagnosis (`install-hud-sdk-jvm` Phase 4).

**A real failure looks like this** (`install-hud-sdk-jvm`, Troubleshooting): endpoints arrive
but the app's functions are missing, because `-Dio.hud.includeDomains` doesn't cover that
package root. On a Kotlin service with an agent older than v0.3.0, functions arrive but
coroutine work is attributed to the wrong caller or to nothing: upgrade the agent.

## Metric flush

Every few seconds. The run needs nothing set.

## Forensics

- **Switched on by** the account's remote config, or the install's own agent settings. The
  run doesn't change either.
- **Caps:** 25 error-based investigations per 6 h per process. Dedup is 2 per (flow, error
  shape) per **1 h**.
- **Budget:** the counts live in process memory, and the run doesn't restart the service,
  so what X3's coverage sweep and probes spend is gone for the timed run.
- **Remote config** can tighten all of this.
- **Log lines:** capped forensics show `Max error-based investigations reached` and
  `Max same investigation reached`. Count sent forensics through the MCP (X6's `forensic_counts` query).

## Logging hazards

Read the agent's logs through the mask in `signals.md` before sharing them.

## How the process serves requests (sizing the rate)

A thread pool serves requests, so one slow endpoint doesn't block the others the way it
does on Node or Python. The shared limit is the database connection pool (HikariCP
defaults to 10). A slow query holding connections makes every DB-touching endpoint wait
for one; at the run's rate that only happens on an endpoint that is already slow, so
lower its weight if its p90 climbs during the run.

## Outbound calls the SDK records

JDBC (from agent v0.2.9) and Apache HttpClient are visible; queues partly. Check the agent
version and its supported-library list at its release tag against the app's dependencies
before X2. Don't count a PR's database or HTTP changes as part of its story when Hud can't
see them.

## Metric rows (counting)

X6's report compares one plain entry's Hud count with what the driver sent (its count
check). When you count by hand, check an entry that returns mixed statuses the same way.
If the two disagree, trust neither number until the count check explains the difference.

## Endpoint names

- **Spring MVC and Boot:** the mapping templates (`/articles/{slug}`).
- **Servlet:** the `url-pattern`.
- **Ktor:** a manual flow, whose name needs the full routing path, root included. A hook
  that sees only the last segment names every route wrong. Check one name in Hud early.

**Only Spring MVC on a Servlet container, plain Servlet handlers and http4k (v0.3.0) are
auto-covered** (`install-hud-sdk-jvm`). Everything else needs a manual `io.hud.api.Flow`, and
the JVM API has no flow types: every manual flow is a **Custom Flow**. WebFlux, Ktor,
Micronaut, Quarkus and Vert.x routes never appear on the Endpoints page however they are
wrapped, and a wrapped Kafka or RabbitMQ consumer has no queue latency, enqueue time or
message count. Say so before X2: on such a service, scope the run on Custom Flows and name
them with a consistent prefix (`kafka:`, `ktor:`, `job:`). Hud's `Endpoints` table lists
them with type `CUSTOM`.

## Start command and images

`install-hud-sdk-jvm` adds the `-javaagent` to the start command, or `JAVA_TOOL_OPTIONS` where
the command can't change (`bootRun`, a base image, a platform-owned launcher):

```bash
java -javaagent:./hud-sdk.jar -Dio.hud.includeDomains=com.acme -jar app.jar
JAVA_TOOL_OPTIONS="-javaagent:/app/hud-sdk.jar -Dio.hud.includeDomains=com.acme"
```

`-javaagent` comes before `-jar`. `includeDomains` is a `;`-separated list of package
prefixes and must name every root that holds the app's code. Keep any existing `-javaagent`
flags. Manual flows also need `io.hud:hud-api` from Maven Central on the **runtime**
classpath, never `compileOnly` or `provided`. **Several JVM processes in one image** (an API and a consumer) are one install
each: wrap every process on the request path, and decide before X2.

## PR ranking (X2)

- **Paths.** Check how Hud records a JVM function's `file` on the first query: a source
  path (`src/main/java/org/x/OwnerController.java`), a package path (`org/x/OwnerController.java`)
  or a bare file name. The ranker matches the first two by suffix; bare names match only
  when unique. If no PR is eligible, look at five rows of `hud-files.json` first.
- **Lazy class loading.** A class is declared when it is first loaded, so a controller
  nobody has called yet may be missing. If the list looks short, run the files query again
  after X3's sweep.
- **Tracing to a route** (`02-choose-pr/SKILL.md` §4). Spring wires beans by interface:
  when the changed method is on a `@Service` implementation, search for its interface's
  name in constructors and `@Autowired` fields, then the `@GetMapping`/`@PostMapping`
  method that calls it. The path is the class-level `@RequestMapping` plus the method's
  mapping, plus `server.servlet.context-path` if it is set. `@EventListener`,
  `@KafkaListener`, `@RabbitListener` and `@Scheduled` methods are non-HTTP entry points.
  On a running Spring Boot app, `/actuator/mappings` settles any path you can't resolve.
  Ktor nests `route("/x") { }` blocks; http4k binds with `"/x" bind GET`.
- **Kotlin consumers** don't look like Java ones: a coroutine loop is `while (isActive)`
  inside a `launch { }`, not `while (true)` in a `Thread`. Find them by shape (the client
  construction, the loop, the launch site), as `install-hud-sdk-jvm`'s Kotlin section does.
