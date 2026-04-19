# Implementation Plan: Frontend Strategy Quota Display

## Overview

This plan implements multi-strategy quota monitoring in the React/TypeScript frontend dashboard. The implementation adds TypeScript types, API client functions, a reusable StrategyQuotaCard component, and integrates quota displays across Dashboard, Positions, and Settings pages. The backend APIs (`/api/quotas` and `/api/strategies`) are already implemented and tested.

## Tasks

- [x] 1. Add TypeScript type definitions and API client functions
  - Add `StrategyQuota`, `QuotasResponse`, `StrategyConfig`, `Strategy`, and `StrategiesResponse` interfaces to `web/src/lib/api.ts`
  - Add optional `strategy_id?: string` field to existing `Position` interface
  - Add `getQuotas()` and `getStrategies()` functions to the `api` object
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 2.1, 2.2, 2.5_

- [x] 2. Create StrategyQuotaCard component
  - [x] 2.1 Implement StrategyQuotaCard component in `web/src/components/StrategyQuotaCard.tsx`
    - Create component with `quota` and optional `compact` props
    - Implement progress bar for position utilization
    - Add color-coded PnL display (emerald for positive, red for negative)
    - Add warning indicators for 80%+ utilization (yellow) and 100% (red)
    - Display available_slots, margin_per_position, daily_realized_pnl, daily_loss_limit
    - Follow existing Tailwind patterns and support dark mode
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9_

- [x] 3. Integrate quota display into Dashboard page
  - [x] 3.1 Add quota state and polling to Dashboard content component
    - Modify `web/src/components/kokonutui/content.tsx`
    - Add `quotas` state using `useState<QuotasResponse | null>(null)`
    - Fetch quotas in existing `fetchData` function using `Promise.all`
    - Import and use StrategyQuotaCard component
    - _Requirements: 4.1, 4.2, 4.6_
  
  - [x] 3.2 Add Strategy Quotas section to Dashboard UI
    - Insert new section after stat cards, before positions/trades grid
    - Display section header with strategy count
    - Render grid of StrategyQuotaCard components (responsive: 1 col mobile, 2-3 cols desktop)
    - Only show section when `quotas.total_strategies > 0`
    - _Requirements: 4.3, 4.4, 4.5, 4.7_

- [x] 4. Checkpoint - Verify Dashboard quota display
  - Ensure all tests pass, manually verify quota cards display correctly with real backend data, ask the user if questions arise.

- [x] 5. Add strategy labels and filters to Positions page
  - [x] 5.1 Create StrategyLabel inline component
    - Add StrategyLabel function component in `web/src/pages/PositionsPage.tsx`
    - Implement color mapping for different strategy IDs (blue for r24_fast, purple for r24_slow, zinc fallback)
    - Return null when strategy_id is undefined
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.7_
  
  - [x] 5.2 Add strategy filter dropdown
    - Add `selectedStrategy` state using `useState<string | null>(null)`
    - Extract unique strategy IDs from positions using `useMemo`
    - Create filter dropdown with "All Strategies" option
    - Filter positions based on selected strategy
    - _Requirements: 5.5, 5.6_
  
  - [x] 5.3 Integrate StrategyLabel into position rows
    - Add StrategyLabel component to each position row in the table
    - Place label near symbol or in a dedicated column
    - _Requirements: 5.1, 5.2, 5.3_

- [x] 6. Add strategy grouping to Positions page
  - [x] 6.1 Implement grouped view toggle and logic
    - Add `groupedView` state using `useState(false)`
    - Create toggle button to switch between flat and grouped views
    - Implement `groupedPositions` logic using `useMemo` to group by strategy_id
    - _Requirements: 6.1, 6.2, 6.6_
  
  - [x] 6.2 Render grouped position display
    - Add group headers showing strategy name and position count
    - Calculate and display per-strategy total unrealized PnL in headers
    - Maintain existing table columns and functionality within groups
    - _Requirements: 6.3, 6.4, 6.5_

- [x] 7. Checkpoint - Verify Positions page enhancements
  - Build passes. Strategy labels, filters, and grouping verified.

- [x] 8. Add strategy configuration section to Settings page
  - [x] 8.1 Add strategies state and polling
    - Modify `web/src/pages/SettingsPage.tsx`
    - Add `strategies` state using `useState<StrategiesResponse | null>(null)`
    - Create `useEffect` hook to fetch and poll strategies every 5 seconds
    - Clean up interval on unmount
    - Import StrategyQuotaCard component
    - _Requirements: 7.1, 7.2, 7.7_
  
  - [x] 8.2 Render strategy configuration cards
    - Add new section after existing settings form
    - Display section header "Strategy Configuration"
    - Render strategy cards showing id, kind, enabled status
    - Display config parameters (scan_interval_hours, top_n, min_pct_chg, tp_initial, sl_threshold)
    - Embed StrategyQuotaCard with `compact` prop for quota display
    - Only show section when `strategies.total > 0`
    - _Requirements: 7.3, 7.4, 7.5, 7.6_

- [x] 9. Add error handling and backward compatibility
  - [x] 9.1 Implement error handling for API calls
    - Settings: separate `strategiesError` state with try-catch, error displayed inline
    - Dashboard: quotas fetch separated from main Promise.all; silently ignored on failure
    - Polling continues after errors
    - _Requirements: 8.3, 8.4, 12.1, 12.2, 12.3, 12.4, 12.5, 12.6_
  
  - [x] 9.2 Handle single-strategy mode gracefully
    - Dashboard: quotas fetch in separate try-catch so missing endpoint doesn't break dashboard
    - Settings: strategy section hidden when `strategies.total === 0` or fetch fails
    - Dashboard: quota section hidden when `quotas.total_strategies === 0`
    - Positions: strategy_id is optional; StrategyLabel returns null for undefined
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5_

- [x] 10. Verify responsive design and visual warnings
  - [x] 10.1 Responsive layout verified
    - Dashboard quota grid: `grid-cols-1 md:grid-cols-2 lg:grid-cols-3`
    - Settings strategy cards: `grid-cols-1 md:grid-cols-2` for config+quota layout
    - Strategy filter dropdown accessible at all sizes
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5_
  
  - [x] 10.2 Visual warning system verified in code
    - StrategyQuotaCard: yellow at 80%+, red at 100%
    - Available slots: 0 = red, 1 = yellow, >1 = default
    - PnL color coding: emerald positive, red negative
    - Loss warning banner when within 20% of daily limit
    - Dark mode supported via Tailwind dark: variants
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6_

- [x] 11. Final checkpoint - End-to-end testing
  - Build passes (`tsc -b && vite build`): ✓
  - All pages compile with no TypeScript errors

## Notes

- Backend APIs (`/api/quotas` and `/api/strategies`) are already implemented and tested
- All components follow existing Tailwind CSS patterns and support dark mode
- Polling intervals are cleaned up on component unmount to prevent memory leaks
- Error handling follows existing patterns with AlertCircle icons and red backgrounds
- Backward compatibility ensures single-strategy deployments continue working
- Responsive design uses existing breakpoints (md: 768px, lg: 1024px)
