# Development of an Automated Prediction Market Strategy Bot for Bayse Markets

## Introduction and Operational Context

The evolution of algorithmic trading has expanded beyond traditional equities and foreign exchange markets, establishing a formidable presence within decentralized and centralized prediction markets. Bayse Markets, positioned as Africa’s largest prediction market infrastructure following its transition from Gowagr, represents a paradigm shift in how localized and global information is financially traded. 

Operating without traditional bookmaker margins or house edges, the platform facilitates peer-to-peer trading across binary outcomes—allowing participants to acquire "YES" or "NO" positions on highly specific real-world events. 

The markets hosted on Bayse encompass diverse categories. In the financial sector, participants trade directional movements on the United States Dollar against the Nigerian Naira (USD/NGN), the hourly trajectories of GBP/USD and EUR/USD, Central Bank of Nigeria (CBN) interest rate decisions, and rapid fifteen-minute Bitcoin price intervals. The sports category includes extensive coverage of the English Premier League (EPL), the Champions League, and the Africa Cup of Nations (AFCON), while the political and cultural sectors cover African governorship races, global elections, and entertainment streaming milestones. 

The objective of this architectural blueprint is the systematic design and deployment of a high-performance, fully automated trading strategy bot specifically engineered for the Bayse Markets API. The bot is required to implement a data-driven strategy that continuously processes market signals, operates fluidly within stringent application programming interface (API) rate limits, features an aggressive risk-management engine, and executes orders autonomously. The architecture must strictly separate the analytical logic from the execution mechanics, utilizing advanced asynchronous network protocols to capture fleeting inefficiencies in the Bayse order books. This exhaustive report serves as the definitive technical specification for engineering the system.

## Infrastructure and Development Environment

The physical and logical deployment environment of the trading bot dictates its capacity to compete in latency-sensitive prediction markets. 

To ensure the bot remains perpetually online to capture sudden macro-economic shifts or sports events, it must be hosted on a high-availability Virtual Private Server (VPS) positioned in topological proximity to the Bayse backend infrastructure. Relying on consumer-grade hardware or localized networks introduces unacceptable points of failure regarding power stability, bandwidth saturation, and physical latency.

### The Python Ecosystem Paradigm

The recommended programming language for this system is Python, selected due to its unparalleled ecosystem for quantitative analysis, asynchronous networking, and structural abstraction. The bot's architecture should mirror the design patterns established by universal cryptocurrency trading libraries, such as CCXT, which provide unified interfaces for disparate exchange APIs. By structuring the Bayse API interactions into a unified class interface—abstracting methods like fetch*order_book, create_order, and cancel_order—the developer ensures modularity and testability. 

Furthermore, the data processing framework must heavily leverage the Pandas library. Prediction market data, particularly historical price series and orderbook depths, requires high-velocity vectorization. Pandas enables the bot to ingest massive arrays of historical pricing data, compute rolling probabilities, calculate market volatility, and align disparate time-series arrays without the computational overhead of iterative Python loops. The asynchronous capabilities natively provided by Python’s asyncio module are equally critical, allowing the bot to maintain persistent WebSocket connections while concurrently calculating predictive models and firing RESTful API requests.

## System Architecture and Connectivity Protocol

The bot must communicate with the Bayse infrastructure via a dual-protocol architecture: Representational State Transfer (REST) over the Hypertext Transfer Protocol Secure (HTTPS) for state-altering actions (order management), and WebSockets for the real-time ingestion of market state data. The base URL for the primary API is https://relay.bayse.markets.

### Connection Pooling and Advanced Socket Management

When the execution layer is instructed to place, amend, or cancel orders rapidly, establishing a new Transmission Control Protocol (TCP) and Transport Layer Security (TLS) handshake for every individual request introduces catastrophic network latency and exhausts the host operating system’s file descriptors. The system must implement persistent connection pooling. Utilizing the aiohttp library in Python allows for the creation of a persistent ClientSession. However, the default configuration of the aiohttp. TCPConnector permits up to 100 concurrent connections. The Bayse API enforces strict rate limits: read operations are restricted to 30 requests per second, and write operations are capped at 20 requests per second per API key. Attempting to open 100 concurrent connections will instantly trigger network throttling and result in HTTP 429 Too Many Requests errors. The bot developer must explicitly configure the limit_per_host parameter within the TCP connector to precisely mirror the Bayse rate limit thresholds, ensuring that the bot naturally queues internal requests rather than bombarding the remote server.

### Rate Limit Throttling and HTTP 429 Handling

Even with connection pooling appropriately configured, the volume of signals generated by the bot during volatile market events—such as an unexpected goal in an EPL match or a sudden shift in the USD/NGN exchange rate—may exceed the 20 requests per second write ceiling. The Bayse infrastructure protects its matching engines by responding to limit breaches with a 429 status code and a Retry-After header. The JSON error payload specifically includes a retryAfter field indicating the exact number of seconds the bot must wait before resuming transmissions. The networking module must implement an interceptor or middleware pattern that scrutinizes every incoming HTTP response. If a 429 status is detected, the bot must immediately halt the specific execution queue, extract the retryAfter integer, and utilize asyncio.sleep() to pause operations for the exact duration requested. Failure to respect the retryAfter parameter typically results in extended IP bans or session revocations.

### Traceability and Debugging Mechanics

A core requirement of the blueprint dictates that the bot must be thoroughly debuggable. When an automated system places thousands of orders daily, identifying the specific systemic failure that caused a rejected order is highly complex. To facilitate backend debugging, Bayse supports custom request tracing. The Execution Layer must dynamically generate a unique string identifier—typically a universally unique identifier (UUID) combined with a local timestamp—and append it to the x-trace-id header of every outbound REST request. If a trade fails due to an obscure matching engine error or a payload formatting issue, the custom trace ID is echoed back within the response headers. The developer can log this identifier locally and cross-reference it with the Bayse engineering team to extract the exact server-side log, significantly accelerating the resolution of integration bugs.

## Cryptographic Authentication Subsystem

The Bayse Markets API implements a robust security model to protect user capital and ensure the immutability of trade instructions. While public endpoints require no authentication, and read-only endpoints mandate only the inclusion of an API key via the X-Public-Key header, all write-heavy operations—such as placing orders or cancelling positions—require a complex cryptographic signature protocol utilizing the Hash-based Message Authentication Code (HMAC) combined with the Secure Hash Algorithm 256 (SHA-256).

### API Key Infrastructure and Management

API keys are issued as cryptographic pairs. The public key (prefixed with pk_live*) functions as the identifier and is safe to transmit openly within headers. The secret key (prefixed with sk*live*) is highly classified, utilized exclusively for offline signature generation, and is displayed to the user only once upon creation. Because the bot operates autonomously on a remote VPS, the secret key must never be hardcoded into the Python source code or committed to a version control repository. It must be injected into the application memory at runtime via encrypted environment variables or a dedicated secrets management daemon. 

Furthermore, the Bayse API allows for programmatic management of these credentials. By calling the POST /v1/user/login endpoint with account credentials, the bot can acquire a session token (x-auth-token) and a device ID (x-device-id). These session headers unlock the ability to call the POST /v1/user/me/api-keys/{keyId}/rotate endpoint, which securely revokes the existing secret key and issues a new one while maintaining the same public identifier. A sophisticated bot architecture will automate this key rotation sequence during known periods of low market volatility, ensuring that even if the VPS memory is compromised, the stolen keys become rapidly obsolete. The rotation endpoint shares a rate limit with key revocation, strictly capped at two requests per thirty minutes per session.

### Constructing the HMAC-SHA256 Payload

The exact procedure for authenticating a write request demands strict adherence to string formatting. The bot must calculate a unique signature for every single order payload to prevent replay attacks and man-in-the-middle interceptions.The process initiates by capturing the current Unix timestamp in seconds. The Bayse server enforces a strict five-minute validation window; if the timestamp submitted in the request deviates from the server's clock by more than five minutes, the API rejects the request with a timestamp*expired error. The bot's host server must run a Network Time Protocol (NTP) daemon to prevent clock drift.Next, the bot must construct the signing payload, which is rigidly formatted as {timestamp}.{METHOD}.{path}.{bodyHash}.
- **The timestamp** is the exact integer sent in the header.
- **The METHOD** is the uppercase HTTP verb (e.g., POST or DELETE).
- **The path** is the exact Universal Resource Identifier (URI) path (e.g., /v1/pm/events/{eventId}/markets/{marketId}/orders).
- **The bodyHash** represents the SHA-256 hexadecimal digest of the raw request body bytes.Calculating the bodyHash is a common point of failure in automated systems. The Python bot must serialize the execution payload dictionary into a JSON string using strict separators (e.g., eliminating unnecessary whitespace). It must then compute the SHA-256 hash of that exact string, convert the digest to hexadecimal, and append it to the payload. If the operation is a DELETE request with no body, the bodyHash evaluates to an empty string, leaving the payload string terminating with a literal period character.Finally, the bot computes the HMAC-SHA256 of the entire payload string using the secret key as the cryptographic seed. The resulting binary output is then encoded using Base64. The resulting string is injected into the HTTP request via the X-Signature header, alongside the X-Timestamp and X-Public-Key headers. Any discrepancy in encoding formats—such as accidentally base64-encoding the body hash or hex-encoding the final signature—will immediately trigger an invalid_signature response from the exchange.

## Data Pipeline: The Market Observer

The success of a systematic strategy relies entirely on the velocity and accuracy of the data it ingests. The bot must feature an independent, asynchronous module defined as the "Market Observer." This module is strictly responsible for maintaining network connectivity, consuming high-throughput data streams, structuring that data into an internal memory map, and alerting the analysis layer to critical updates.

### Targeted Event Categorization

The Market Observer must be configured to filter and monitor specific event categories relevant to the bot's underlying statistical models. Bayse provides distinct data topologies based on the real-world subject matter:Financial Markets (FX & Macro): Tracking the USD/NGN directional markets, EUR/USD hourly prints, and Central Bank of Nigeria rate decisions. These markets require the observer to pull correlated external data from legacy foreign exchange APIs to measure real-time discrepancies. Crypto Markets: Monitoring the volatile fifteen-minute Bitcoin (BTC) and Solana (SOL) price level markets. Given the constant movement of the underlying assets, the observer must map the Bayse order book updates against the continuous spot prices streaming from platforms like Binance or Coinbase. Sports Markets: Subscribing to English Premier League, Champions League, and AFCON outcomes, tracking match performances and player statistics without the dampening effect of traditional bookmaker margins.

### WebSocket Integration and Channel Topography

The Bayse infrastructure streams market data through two distinct WebSocket endpoints. The public endpoint, located at wss://socket.bayse.markets/ws/v1/markets, requires no authentication and streams generalized market data. The private endpoint, positioned at wss://socket.bayse.markets/ws/v1/user, streams proprietary execution reports regarding the bot's own orders and requires an authentication object containing the API key to be passed with every subscription message. 

To build a holistic view of the market, the Market Observer must open connections to both endpoints and subscribe to specific channels. The orderbook channel provides real-time snapshot updates representing the current depth of bids and asks for a specific market. The prices channel isolates pure ticker movements, while the activity channel broadcasts every buy and sell order executed across the event. Concurrently, the private orders channel emits order_updated events, which are essential for tracking when the bot's own resting liquidity transitions from open to partial_filled or filled. The WebSocket connection enforces a strict message rate limit of 10 messages per second per connection; exceeding this threshold will result in server-side rate limit errors without closing the socket, requiring the bot to throttle its outgoing subscription messages.

### Network Resilience, Jitter, and Exponential Backoff

WebSocket connections are inherently persistent but notoriously fragile across public internet infrastructure. Connection closures, missed ping-pong heartbeats, and transient server restarts are inevitable. The Market Observer must be engineered with fault tolerance as a primary objective. 

When the websockets client in Python detects a fatal disconnection, it must initiate a reconnection loop. However, the bot must not immediately hammer the server with reconnection attempts. Attempting rapid, continuous reconnections mimics a Distributed Denial of Service (DDoS) attack and will trigger IP-level bans. Instead, the system must utilize an exponential backoff algorithm. The logic mandates that the first retry occurs after a brief delay (e.g., three seconds). If that connection fails, the delay doubles to six seconds, then twelve, up to a predetermined maximum ceiling (e.g., sixty seconds). To prevent a "thundering herd" scenario—where hundreds of disconnected API clients all attempt to reconnect at the exact same millisecond—the observer must apply cryptographic "jitter," adding a randomized fractional variance to each delay interval.

### State Synchronization Protocol

A critical vulnerability in algorithmic trading occurs during the exact moment a WebSocket connection is lost. During the blackout period, the Bayse matching engines continue to process trades, alter the order book, and potentially fill the bot's resting orders. The bot's internal representation of the market state becomes instantly invalid. 

Upon re-establishing the connection, the Market Observer must execute a rigorous state synchronization protocol before allowing the strategy to resume trading. The protocol dictates that the observer immediately begins queuing all incoming real-time WebSocket messages into an asyncio. Queue without processing them. Simultaneously, the bot dispatches an HTTP GET request to /v1/pm/books to download the definitive, current REST snapshot of the order book. Once the REST snapshot is loaded into the bot's memory, the queue is unblocked. The bot processes the enqueued WebSocket messages, meticulously discarding any messages bearing timestamps or sequence IDs older than the REST snapshot. Furthermore, the bot must query the /v1/pm/portfolio and /v1/pm/orders endpoints to reconcile its own inventory, ensuring it accounts for any orders that were filled while the socket was disconnected.

## The Strategic Engine: Analysis and Execution Segregation

The blueprint explicitly mandates the separation of the Analysis Layer (logic and prediction) from the Execution Layer (API interaction). This architectural design pattern ensures that heavy computational tasks do not block the asynchronous event loop responsible for network communication, and conversely, that network latency does not skew the timing of the predictive algorithms.

### The Analysis Layer: Processing Probability Signals

The Analysis Layer is a purely mathematical module. Its sole function is to ingest the structured data provided by the Market Observer, compute the true probability of a real-world event occurring based on internal proprietary models, and compare that probability against the implied probability offered by the Bayse market. 

Prediction markets operate on binary outcomes, meaning prices normalize elegantly to percentage probabilities. If the market prices a "YES" share at $0.65 USD, it implies a 65% consensus that the event will happen. The Bayse platform utilizes base multipliers depending on the currency: a probability of 0.65 costs $0.65 in the USD market, where a winning share pays out $1.00. However, in the NGN market, which utilizes a 100x multiplier, a 0.65 probability costs ₦65, with a winning share paying out ₦100. The Analysis Layer must inherently account for these multipliers to accurately calculate edge and exposure. The strategy specifies defining specific triggers for taking positions. For example, if the internal model calculates that the true probability of Bitcoin closing above $150,000 by Friday is 80% (0.80), but the current ask price on the Bayse CLOB is 0.70, the Analysis Layer identifies a massive positive expected value (+EV) anomaly. The strategy dictates that an order should be triggered if the internal model's probability estimate deviates from the current market price by >X% (e.g., a >5% edge). When this threshold is breached, the Analysis Layer formulates an abstract signal—detailing the target market, the chosen outcome ("YES"), the maximum acceptable entry price, and the theoretical size—and places it onto a thread-safe asynchronous queue. 

Alternatively, the Analysis Layer can run a spread-capture market-making algorithm. In this mode, the model calculates the exact mid-price between the current best bid and best ask. If the internal model agrees with the mid-price, it generates signals to place bids slightly below the mid and asks slightly above it. When retail participants cross the spread on both sides, the bot captures the difference as profit. If the bot begins accumulating too much inventory on the "YES" side, the Analysis Layer implements "inventory skew," shifting both quotes downward to become a more aggressive seller and a less aggressive buyer, effectively unwinding the position back to neutrality.

### The Execution Layer: Constructing API Interactions

The Execution Layer monitors the signal queue and translates abstract mathematical edges into precise JSON payloads for the Bayse API. It does not perform any predictive analysis; its core responsibilities are payload serialization, cryptographic signing, API dispatch, and order lifecycle tracking. 

To execute a trade, the bot targets the POST /v1/pm/events/{eventId}/markets/{marketId}/orders endpoint. The Execution Layer must meticulously structure the request body. The side must be explicitly designated as BUY or SELL. The outcomeId requires the specific UUID corresponding to "YES" or "NO," which the bot previously extracted by mapping the /v1/pm/events/{eventId} details. The amount parameter dictates the total capital allocated to the trade, which must meet the platform minimums of $1.00 USD or ₦100.00 NGN. The currency must be specified to align with the asset holding. For precision execution, the type is generally set to LIMIT, requiring the inclusion of the specific price parameter ranging from 0.01 to 0.99.

### Engine Mechanics: CLOB vs. AMM

The Execution Layer must dynamically adapt its payload depending on the underlying engine of the targeted event. If the event operates on the Automated Market Maker (AMM) engine, orders are executed instantly against a programmatic liquidity curve at a deterministically calculated price. AMM orders cannot be amended; altering the size or price requires cancelling the original order and submitting a new one. 

Conversely, Central Limit Order Book (CLOB) events require sophisticated Time-In-Force (TIF) management. By default, limit orders utilize the GTC (Good Till Cancel) parameter, meaning they rest on the order book until filled or manually cancelled. If the bot is attempting to capture a fleeting macroeconomic data release, it should utilize the FAK (Fill and Kill) parameter. This instructs the exchange to instantly match as much of the order as possible against existing liquidity and immediately cancel any unfilled remainder, ensuring the bot does not leave stale, unmonitored exposure resting on the book.

### Self-Trade Prevention (STP) Engineering

A high-frequency bot operating on a CLOB framework will continuously update its bids and asks as the underlying probability model shifts. This creates a severe risk of the bot inadvertently crossing its own resting orders. Trading against oneself forces the bot to pay exchange fees to the platform for zero net change in position, destroying capital efficiency.To eliminate this hazard, the Execution Layer must encode Self-Trade Prevention (STP) rules into the order payload via the stpMode parameter.
- **SKIP**: The engine silently bypasses the self-match and leaves both orders on the book. (Not recommended for active strategies).
- **CANCEL_OLDEST**: The resting maker order is cancelled, and the new incoming taker order continues to match against the market. This is the optimal mode for a bot that is continuously re-quoting its prices based on new data.
- **CANCEL_NEWEST**: The incoming taker order is blocked and rejected, leaving the resting maker untouched. This is useful for defending an established, highly favorable position from a rogue internal logic loop.
- **CANCEL_BOTH**: The engine strips both the resting maker and the incoming taker from the order book. This is heavily utilized when the bot realizes its internal signals are contradictory and needs to instantly neutralize exposure.

## The Risk Meter: Absolute Constraints and Capital Preservation

Deploying an automated execution architecture without rigorous systemic constraints exposes the operator to catastrophic, infinite downside risk due to logic bugs, stale data pricing, or flash crashes. The blueprint mandates the inclusion of a comprehensive "Risk Meter" module. This module intercepts every intended action from the Execution Layer and subjects it to strict auditing protocols before permitting network transmission.

### Position Sizing and Portfolio Verification

The primary function of the Risk Meter is to enforce a hard-coded limit on capital allocation per trade. The strategy explicitly dictates: "Never risk more than 2% of total balance on a single trade."

To execute this mandate, the Risk Meter must maintain continuous awareness of the bot's liquidity. Before validating a signal, the module invokes a call to GET /v1/wallet/assets or parses the continuous updates from the /v1/pm/portfolio endpoint. The portfolio endpoint returns a detailed outcomeBalances array, outlining the total shares owned, average price paid, current market value, and unrealized profit and loss (P&L). 

If the total free, unencumbered balance across the wallet equates to ₦1,000,000, the Risk Meter calculates the 2% maximum threshold as ₦20,000. If the Analysis Layer requests a trade utilizing an amount of ₦50,000 to capture a massive perceived edge, the Risk Meter intercepts the payload and aggressively truncates the amount field down to the ₦20,000 maximum. If the strategy prohibits partial scaling, the Risk Meter rejects the signal entirely and logs an internal constraint violation.

### The Global Daily Loss Kill Switch

Algorithmic systems are vulnerable to systemic degradation, where a slightly flawed statistical model or a continuous pattern of adverse selection causes the portfolio to bleed capital incrementally over thousands of tiny trades. To arrest this degradation, the Risk Meter must implement a "Global Daily Loss Limit."

The system aggregates the realized P&L from closed positions and the unrealized P&L from active holdings. If the cumulative net loss for the 24-hour trading session exceeds a predefined critical threshold (e.g., -5% of starting daily equity), the Risk Meter activates an emergency shutdown sequence. The kill switch bypasses all strategic logic and executes a comprehensive routine to eliminate market exposure. It immediately calls the DELETE /v1/pm/orders batch command, stripping every open order off the Bayse CLOB. 

Following the cancellation of resting liquidity, the bot halts all outbound execution capabilities, transitioning into a passive monitoring state. This condition requires manual intervention and code review by the developer before trading can resume.

### Liquidity Filtering and Slippage Avoidance

In prediction markets, certain niche events may feature highly illiquid order books with substantial spreads. Submitting a large market order—or a limit order aggressively crossing the spread—into a thin market will cause the order to consume the top level of liquidity and sweep deep into unfavorable price tiers, resulting in massive slippage and the destruction of the mathematical edge. The blueprint requires the integration of a Liquidity Filter. 

Before transmitting an order, the Risk Meter accesses the orderbook_update data maintained by the Market Observer. The module simulates the execution of the requested amount against the cumulative volume resting at each price tier of the current book. If fulfilling the total order size requires sweeping the price beyond an acceptable variance parameter (e.g., more than a 2% deviation from the current best bid/ask), the Risk Meter blocks the order. 

Furthermore, the Execution Layer must strictly utilize the API's native safeguards. For market orders or FAK sweeps, the payload must include the maxSlippage parameter (a float between 0.00 and 1.00). If the Bayse matching engine determines the trade would violate this slippage boundary, it rejects the execution. When the bot intends to act exclusively as a market maker providing liquidity, it must append the postOnly=true boolean to the payload. This ensures that if the bot's calculated limit price accidentally crosses the spread due to a timing delay, the matching engine will reject the order entirely rather than forcing the bot to pay taker fees.

### Exposure Neutralization via Minting and Burning

A unique mechanical feature of binary prediction markets is the capability to manipulate exposure through minting and burning complementary shares. Because a specific market outcome will exclusively resolve as either true or false, a paired "YES" share and a "NO" share are mathematically guaranteed to pay out exactly 1.00 base unit ($1.00 or ₦100). 

If a spread-capture bot operates effectively, it will eventually accumulate fills on both sides of the order book. When it holds an equal inventory of "YES" and "NO" shares, that capital is effectively locked in a delta-neutral position. The Risk Meter must continuously scan the portfolio for these balanced pairs. Upon detection, the module triggers the POST /v1/pm/markets/{marketId}/burn endpoint. The burning process surrenders the paired shares to the exchange and instantly credits the underlying currency back to the bot's free wallet balance. This operation incurs zero price impact, neutralizes the locked exposure, and recycles capital to fund the 2% position sizing algorithm for future trades.

## Vetting, Validation, and Deployment Protocols

Executing algorithmic code against live, capitalized API keys without extensive empirical validation is a violation of institutional engineering standards. The bot infrastructure must feature sophisticated environments for both historical verification and forward-simulated logic testing.

### Historical Backtesting Architecture

The validity of the statistical >X% deviation trigger relies on its performance against historical market conditions. The bot ecosystem must include a backtesting module built utilizing Pandas DataFrames. The backtester queries the Bayse GET /v1/pm/events/{eventId}/price-history endpoint to download vast arrays of historical price intervals. The developer feeds historical external data signals (such as past currency fluctuations or sports scores) into the isolated Analysis Layer. The engine computes what probability it would have generated at that exact historical timestamp and compares it against the Bayse price history. To maintain statistical integrity, the backtester must aggressively penalize its own results, deducting exchange transaction fees from every simulated profit and accounting for assumed slippage during periods of low historical volume.

### Mandatory "Dry Run" Execution Mode

While backtesting validates the mathematical edge, it completely ignores the realities of network latency, asynchronous event loop blocking, API payload serialization errors, and WebSocket state mismanagement. To test the software engineering itself, the bot must include a mandatory "Dry Run" mode. 

When initialized with the environment variable DRY_RUN=True, the bot connects to the live production environment. The Market Observer processes real-time WebSocket data, the Risk Meter audits balances, and the Analysis Layer generates true execution signals. However, at the final stage of the Execution Layer, the bot intercepts the HTTP dispatch. Instead of utilizing the secret key to sign and transmit a payload to the /orders endpoint, the bot logs the intended payload locally. 

The Dry Run module then monitors the live WebSocket orderbook feed. It tracks the simulated limit order, patiently waiting to see if the actual public market price ever trades through the simulated limit price. When an intersection occurs, the bot records a "simulated fill," contrasting the expected execution against the actual real-time liquidity available on Bayse at that precise millisecond. This process provides an irrefutable, risk-free audit trail proving that the complex software architecture functions identically to the theoretical models before a single Naira or Dollar is placed at risk.

## Implementation Guide: Architectural Dos and Don'ts

| Operational Domain | Critical Action (Do) | Prohibited Action (Don't) |
| :--- | :--- | :--- |
| API Management | Dynamically configure the aiohttp TCP connection pool limit to strictly mirror the Bayse 20/30 requests per second rate limits. | Do not use the default 100-connection limit, as this will trigger immediate network throttling and HTTP 429 bans. |
| Authentication | Automate the programmatic rotation of API keys utilizing the /v1/user/me/api-keys/{keyId}/rotate endpoint via session tokens. | Do not hardcode the sk_live*... secret key into the Python source code or commit it to GitHub. |
| Request Signing | Generate the bodyHash by executing a SHA-256 digest on the exact raw JSON string bytes formatted for the request. | Do not modify, trim whitespace, or reformat the JSON payload after generating the HMAC-SHA256 signature. |
| Data Ingestion | Implement exponential backoff with randomized cryptographic jitter to recover from dropped WebSocket connections gracefully. | Do not execute trade logic immediately after a reconnection without first downloading a REST snapshot to clear stale state. |
| Order Management | Inject unique x-trace-id headers into every outbound execution payload to ensure debugging support from the Bayse backend. | Do not submit large arrays of orders in batches without calculating the rate limit cost; batches are charged per item, not per request. |
| Risk Constraints | Utilize the maxSlippage parameter for market sweeps and the postOnly parameter for limit orders providing liquidity. | Do not allow the Analysis Layer to execute trades without the Risk Meter dynamically verifying the 2% capital allocation limit against the /v1/wallet/assets query. |
| Self-Trade Prevention | Actively utilize the stpMode parameter (e.g., CANCEL_OLDEST) when running market-making or continuous re-quoting strategies. | Do not default to SKIP on a high-frequency strategy, as this will result in the bot repeatedly crossing its own orders and paying unnecessary fees. |
| Capital Efficiency | Continuously scan the portfolio for matched pairs of "YES" and "NO" shares and execute the burn endpoint to reclaim liquid capital. | Do not leave locked, delta-neutral inventory resting indefinitely while the bot starves for free margin to execute new signals. |

The synthesis of rigorous mathematical probability modeling, asynchronous networking resilience, and draconian risk constraints forms the foundation of a highly competitive automated trading system. By strictly adhering to the architectural directives outlined in this report, the deployed bot will seamlessly integrate with the Bayse Markets ecosystem, effectively capitalizing on the diverse financial, sports, and cultural prediction markets available on the platform.
