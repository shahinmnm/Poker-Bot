# Navigation System Architecture

## Overview

The poker bot implements a hierarchical menu navigation system with state persistence, breadcrumb trails, and error recovery.

## Menu Hierarchy
```
MAIN (root)
├── PRIVATE_MENU
│   ├── WALLET
│   ├── STATS
│   └── SETTINGS
├── GROUP_MENU
│   └── ADMIN_PANEL
└── HELP
    └── RULES
```

## State Management

### MenuState Schema
```python
@dataclass
class MenuState:
    chat_id: int
    location: str  # MenuLocation enum value
    context_data: Dict[str, Any]
    timestamp: float
```

### Redis Storage
- **Key Pattern:** `menu_state:{chat_id}`
- **TTL:** 3600 seconds (1 hour)
- **Serialization:** JSON

## State Recovery

The system automatically:
- Validates enum values
- Corrects future timestamps
- Resets stale states (>24 hours)
- Repairs invalid `context_data`

## Navigation Flow

### Back Button
1. Get current `MenuState` from Redis
2. Look up parent in `MENU_HIERARCHY`
3. Create new `MenuState` with parent location
4. Persist to Redis
5. Re-render menu with new context

### Home Button
1. Clear `MenuState` from Redis
2. Create new `MenuState` with `MAIN` location
3. Re-render menu

## Breadcrumb Trail

- Rendered at top of menu messages
- Format: `📍 Main → Private → Wallet`
- RTL support for Persian (reversed separator)
- Cached for performance

## Performance Metrics

The middleware tracks:
- `total_navigations`: Total navigation actions
- `back_actions`: Back button presses
- `home_actions`: Home button presses
- `avg_build_time_ms`: Average context build time

Metrics are logged every 5 minutes if activity is detected.

## Error Handling

### Graceful Degradation
- Corrupted state → Reset to `MAIN`
- Invalid location enum → Reset to `MAIN`
- Redis connection failure → Default to stateless behavior
- Translation key missing → Use enum value as fallback

### User Feedback
- Navigation errors show alert: `⚠️ Navigation failed`
- State recovery is silent (logged only)
- Slow builds (>100ms) trigger warning logs

## Translation Keys

### Required Keys
- `ui.nav.back`: Back button text
- `ui.nav.home`: Home button text
- `ui.menu.location.*`: Location labels (9 required)
- `ui.error.navigation_failed`: Error message
- `ui.error.invalid_state`: Invalid state message

### Example (English)
```json
{
  "ui": {
    "nav": {
      "back": "◀️ Back",
      "home": "🏠 Home"
    },
    "menu": {
      "location": {
        "main": "Main Menu",
        "private_menu": "Private Games",
        "wallet": "Wallet"
      }
    }
  }
}
```

## Testing Checklist
- [ ] Navigate `MAIN → PRIVATE_MENU → WALLET → Back → Back`
- [ ] Press Home from any deep location
- [ ] Verify breadcrumb rendering in all 4 languages
- [ ] Test with corrupted Redis state
- [ ] Test with missing parent in hierarchy
- [ ] Measure average build time (<50ms expected)
- [ ] Test state persistence across bot restart
- [ ] Verify RTL breadcrumb separator for Persian

## Future Enhancements
- Deep Linking: Direct navigation to any location
- State History: Undo/redo navigation
- Favorites: Pin frequently used locations
- Search: Jump to location by keyword
