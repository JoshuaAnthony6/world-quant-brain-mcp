# WorldQuant Brain MCP Examples

This directory contains examples of how to use the WorldQuant Brain MCP server with AI assistants.

## Example Conversations with Claude

### Example 1: Submitting a Simple Alpha

**User**: "Can you submit a simple rank-based alpha to WorldQuant Brain?"

**Claude** (using the MCP server):
```
I'll submit a rank(close) alpha to WorldQuant Brain.

[Calls submit_alpha tool with parameters:
{
  "alpha_expression": "rank(close)",
  "region": "USA",
  "universe": "TOP3000",
  "delay": 1
}]

Result: Alpha submitted successfully! Your alpha has been simulated with a Sharpe ratio of 0.85...
```

### Example 2: Checking Performance of Existing Alphas

**User**: "Show me my top 5 alphas"

**Claude** (using the MCP server):
```
I'll retrieve your top 5 alphas.

[Calls list_alphas tool with parameters:
{
  "limit": 5
}]

Here are your top 5 alphas:
1. Alpha ID: abc123 - Status: ACTIVE - Sharpe: 1.2
2. Alpha ID: def456 - Status: ACTIVE - Sharpe: 1.0
...
```

### Example 3: Developing a Momentum Strategy

**User**: "Help me create a momentum-based trading alpha"

**Claude** (using the MCP server):
```
I'll help you create a momentum strategy. Let me submit a momentum alpha that looks at 
5-day price changes.

[Calls submit_alpha tool with parameters:
{
  "alpha_expression": "rank(close / ts_delay(close, 5) - 1)",
  "region": "USA",
  "universe": "TOP3000",
  "delay": 1
}]

The momentum alpha has been submitted. The simulation shows:
- Sharpe Ratio: 0.92
- Returns: 8.5% annualized
- Turnover: 45%
...
```

## Example Alpha Expressions

### Basic Alphas

1. **Simple Rank**
   ```
   rank(close)
   ```
   Ranks stocks by their closing prices.

2. **Volume Rank**
   ```
   rank(volume)
   ```
   Ranks stocks by trading volume.

### Momentum Alphas

3. **5-Day Momentum**
   ```
   close / ts_delay(close, 5) - 1
   ```
   5-day price change momentum.

4. **20-Day Momentum with Rank**
   ```
   rank(close / ts_delay(close, 20) - 1)
   ```
   Ranked 20-day momentum.

### Mean Reversion Alphas

5. **Z-Score**
   ```
   (close - ts_mean(close, 20)) / ts_std_dev(close, 20)
   ```
   Standardized deviation from 20-day moving average.

6. **Negative Z-Score (Mean Reversion)**
   ```
   -1 * (close - ts_mean(close, 20)) / ts_std_dev(close, 20)
   ```
   Short overextended stocks, long undervalued.

### Volume-Based Alphas

7. **Volume-Price Correlation**
   ```
   rank(ts_corr(volume, close, 10))
   ```
   10-day correlation between volume and price.

8. **Volume Spike with Price Change**
   ```
   rank(volume / ts_mean(volume, 20)) * rank(close / ts_delay(close, 1) - 1)
   ```
   Combines volume spikes with price momentum.

### Technical Indicator Alphas

9. **RSI-Like Indicator**
   ```
   ts_sum(ts_delta(close, 1) > 0 ? ts_delta(close, 1) : 0, 14) / ts_sum(abs(ts_delta(close, 1)), 14)
   ```
   Simplified RSI calculation.

10. **Bollinger Band Position**
    ```
    (close - ts_mean(close, 20)) / (2 * ts_std_dev(close, 20))
    ```
    Position within Bollinger Bands.

## Testing Alphas

To test these alphas with the MCP server:

1. Ensure your credentials are configured
2. Start the MCP server
3. Use Claude Desktop or another MCP client
4. Ask Claude to submit the alpha expressions
5. Review the simulation results

## Tips for Alpha Development

1. **Start Simple**: Begin with basic rank-based alphas
2. **Test Variations**: Try different lookback periods and combinations
3. **Monitor Metrics**: Focus on Sharpe ratio, returns, and turnover
4. **Iterate**: Refine based on simulation results
5. **Diversify**: Create alphas with different strategies (momentum, mean reversion, etc.)

## Regions and Universes

Available regions:
- USA: United States market
- CHN: Chinese market
- EUR: European markets

Available universes:
- TOP3000: Top 3000 stocks by market cap
- TOP500: Top 500 stocks by market cap
- TOP1000: Top 1000 stocks by market cap

Choose based on your investment focus and alpha strategy.
