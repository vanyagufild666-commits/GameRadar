PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 5000;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user_id INTEGER NOT NULL UNIQUE,
    telegram_username TEXT,
    first_name TEXT,
    is_bot_blocked INTEGER NOT NULL DEFAULT 0 CHECK (is_bot_blocked IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    gender TEXT NOT NULL CHECK (gender IN ('m', 'f', 'x')),
    age INTEGER NOT NULL CHECK (age BETWEEN 18 AND 99),
    country_code TEXT NOT NULL,
    city TEXT,
    bio TEXT NOT NULL CHECK (length(bio) BETWEEN 1 AND 1000),
    photo_file_id TEXT,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'active', 'paused', 'banned', 'deleted')),
    is_visible INTEGER NOT NULL DEFAULT 1 CHECK (is_visible IN (0, 1)),
    paused_until TEXT,
    paused_reason TEXT,
    auto_paused_at TEXT,
    last_active_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    published_at TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    card_revision INTEGER NOT NULL DEFAULT 1,
    CHECK (status <> 'active' OR is_visible = 1)
);

CREATE TABLE IF NOT EXISTS profile_languages (
    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    language_code TEXT NOT NULL,
    is_primary INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
    PRIMARY KEY (profile_id, language_code)
);

CREATE TABLE IF NOT EXISTS profile_games (
    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    game_name TEXT NOT NULL,
    PRIMARY KEY (profile_id, game_name)
);

CREATE TABLE IF NOT EXISTS profile_filters (
    profile_id INTEGER PRIMARY KEY REFERENCES profiles(id) ON DELETE CASCADE,
    min_age INTEGER NOT NULL DEFAULT 18 CHECK (min_age BETWEEN 18 AND 99),
    max_age INTEGER NOT NULL DEFAULT 99 CHECK (max_age BETWEEN 18 AND 99),
    geo_mode TEXT NOT NULL DEFAULT 'any' CHECK (geo_mode IN ('any', 'country', 'city')),
    require_common_language INTEGER NOT NULL DEFAULT 1 CHECK (require_common_language IN (0, 1)),
    require_common_game INTEGER NOT NULL DEFAULT 0 CHECK (require_common_game IN (0, 1)),
    only_active_recent INTEGER NOT NULL DEFAULT 0 CHECK (only_active_recent IN (0, 1)),
    search_mode TEXT NOT NULL DEFAULT 'city' CHECK (search_mode IN ('city', 'genre')),
    gender_pref TEXT NOT NULL DEFAULT 'any' CHECK (gender_pref IN ('any', 'same', 'other')),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (min_age <= max_age)
);

CREATE TABLE IF NOT EXISTS likes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    to_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'withdrawn')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (from_user_id, to_user_id),
    CHECK (from_user_id <> to_user_id)
);

CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_a_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    user_b_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'closed', 'blocked')),
    a_consent INTEGER NOT NULL DEFAULT 0 CHECK (a_consent IN (0, 1)),
    b_consent INTEGER NOT NULL DEFAULT 0 CHECK (b_consent IN (0, 1)),
    matched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    closed_at TEXT,
    CHECK (user_a_id < user_b_id),
    UNIQUE (user_a_id, user_b_id)
);

CREATE TABLE IF NOT EXISTS view_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    viewer_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    candidate_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    cycle_no INTEGER NOT NULL DEFAULT 1,
    action TEXT NOT NULL CHECK (action IN ('shown', 'opened', 'skip', 'like', 'unlike', 'expired', 'mutual')),
    shown_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    action_at TEXT,
    cooldown_until TEXT,
    CHECK (viewer_user_id <> candidate_user_id)
);

CREATE TABLE IF NOT EXISTS deck_state (
    viewer_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    candidate_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    cycle_no INTEGER NOT NULL DEFAULT 1,
    impression_count INTEGER NOT NULL DEFAULT 0,
    skip_count INTEGER NOT NULL DEFAULT 0,
    last_action TEXT,
    last_shown_at TEXT,
    last_action_at TEXT,
    next_eligible_at TEXT,
    like_pending_until TEXT,
    is_exhausted INTEGER NOT NULL DEFAULT 0 CHECK (is_exhausted IN (0, 1)),
    PRIMARY KEY (viewer_user_id, candidate_user_id),
    CHECK (viewer_user_id <> candidate_user_id)
);

CREATE TABLE IF NOT EXISTS deck_sessions (
    viewer_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    cycle_no INTEGER NOT NULL,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_served_at TEXT,
    exhausted_at TEXT,
    reset_at TEXT,
    PRIMARY KEY (viewer_user_id, cycle_no)
);

CREATE TABLE IF NOT EXISTS user_blocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    blocker_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    blocked_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reason TEXT,
    UNIQUE (blocker_user_id, blocked_user_id),
    CHECK (blocker_user_id <> blocked_user_id)
);

CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reporter_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    target_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    reason TEXT NOT NULL CHECK (reason IN ('adult', 'toxicity', 'spam', 'fake', 'other')),
    comment TEXT,
    status TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'reviewing', 'resolved', 'rejected')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at TEXT,
    CHECK (reporter_user_id <> target_user_id)
);

CREATE TABLE IF NOT EXISTS message_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    recipient_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    match_id INTEGER REFERENCES matches(id) ON DELETE SET NULL,
    kind TEXT NOT NULL CHECK (kind IN ('text', 'photo', 'video', 'video_note')),
    text_content TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'rejected', 'blocked')),
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    responded_at TEXT,
    CHECK (sender_user_id <> recipient_user_id)
);

CREATE TABLE IF NOT EXISTS message_attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER NOT NULL REFERENCES message_requests(id) ON DELETE CASCADE,
    media_type TEXT NOT NULL CHECK (media_type IN ('photo', 'video', 'video_note')),
    telegram_file_id TEXT NOT NULL,
    caption TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS registration_sessions (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    state TEXT NOT NULL,
    draft_json TEXT NOT NULL DEFAULT '{}',
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    source TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS referrals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    referrer_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    invited_user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    source TEXT,
    rewarded INTEGER NOT NULL DEFAULT 0 CHECK (rewarded IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (referrer_user_id <> invited_user_id)
);

CREATE TABLE IF NOT EXISTS referral_bonuses (
    referral_id INTEGER PRIMARY KEY REFERENCES referrals(id) ON DELETE CASCADE,
    granted_cards INTEGER NOT NULL DEFAULT 10 CHECK (granted_cards >= 0),
    consumed_cards INTEGER NOT NULL DEFAULT 0 CHECK (consumed_cards BETWEEN 0 AND granted_cards),
    confirmed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    notification_sent INTEGER NOT NULL DEFAULT 0 CHECK (notification_sent IN (0, 1))
);

CREATE TABLE IF NOT EXISTS moderation_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    moderator_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    action TEXT NOT NULL CHECK (action IN ('suspend', 'ban', 'reject')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_profiles_pool ON profiles(status, is_visible, last_active_at);
CREATE INDEX IF NOT EXISTS idx_profiles_country_age ON profiles(country_code, age);
CREATE INDEX IF NOT EXISTS idx_profile_languages_lang ON profile_languages(language_code, profile_id);
CREATE INDEX IF NOT EXISTS idx_profile_games_game ON profile_games(game_name, profile_id);
CREATE INDEX IF NOT EXISTS idx_likes_to ON likes(to_user_id, status);
CREATE INDEX IF NOT EXISTS idx_likes_from ON likes(from_user_id, status);
CREATE INDEX IF NOT EXISTS idx_matches_a ON matches(user_a_id, status);
CREATE INDEX IF NOT EXISTS idx_matches_b ON matches(user_b_id, status);
CREATE INDEX IF NOT EXISTS idx_views_viewer ON view_events(viewer_user_id, shown_at);
CREATE INDEX IF NOT EXISTS idx_deck_eligible ON deck_state(viewer_user_id, next_eligible_at);
CREATE INDEX IF NOT EXISTS idx_deck_last_shown ON deck_state(viewer_user_id, last_shown_at);
CREATE INDEX IF NOT EXISTS idx_requests_recipient ON message_requests(recipient_user_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_requests_sender ON message_requests(sender_user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_events_user ON events(user_id, name, created_at);
