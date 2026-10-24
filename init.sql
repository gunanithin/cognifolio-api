-- Cognifolio — Initial Database Schema
-- Production PostgreSQL DDL (Supabase / Managed PostgreSQL)

-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================
-- USERS & ORGANIZATIONS
-- ============================================

CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    avatar_url TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE organizations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name TEXT NOT NULL,
    slug TEXT UNIQUE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE org_members (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('admin', 'member', 'viewer')),
    joined_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(org_id, user_id)
);

-- ============================================
-- DOCUMENTS
-- ============================================

CREATE TABLE documents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    org_id UUID REFERENCES organizations(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    file_path TEXT NOT NULL DEFAULT '',
    file_url TEXT,                          -- Cloud Storage path
    file_size_bytes BIGINT DEFAULT 0,
    file_type TEXT NOT NULL,                -- pdf, png, jpg, etc.
    mime_type TEXT,
    page_count INT DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'uploading' 
        CHECK (status IN ('uploading', 'processing', 'ready', 'failed')),
    error_message TEXT,
    retention_days INT DEFAULT 90,
    expires_at TIMESTAMP WITH TIME ZONE,
    uploaded_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    processed_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX idx_documents_user_id ON documents(user_id);
CREATE INDEX idx_documents_org_id ON documents(org_id);
CREATE INDEX idx_documents_status ON documents(status);
CREATE INDEX idx_documents_expires_at ON documents(expires_at);

-- ============================================
-- EXTRACTED INSIGHTS
-- ============================================

CREATE TABLE document_insights (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    document_type TEXT,                     -- invoice, contract, report, letter, etc.
    title TEXT,
    summary TEXT,
    key_points JSONB DEFAULT '[]'::jsonb,   -- ["point 1", "point 2", ...]
    entities JSONB DEFAULT '{}'::jsonb,     -- {"vendor": "...", "amount": "...", ...}
    line_items JSONB DEFAULT '[]'::jsonb,   -- [{"description": "...", "amount": ...}, ...]
    action_items JSONB DEFAULT '[]'::jsonb, -- ["action 1", ...]
    risk_clauses JSONB DEFAULT '[]'::jsonb, -- ["penalty clause", ...]
    charts_data JSONB DEFAULT '{}'::jsonb,  -- {"labels": [...], "values": [...]}
    raw_extracted_text TEXT,
    confidence_score FLOAT DEFAULT 1.0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_insights_document_id ON document_insights(document_id);
CREATE INDEX idx_insights_document_type ON document_insights(document_type);

-- ============================================
-- AUDIT LOG
-- ============================================

CREATE TABLE audit_log (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    org_id UUID REFERENCES organizations(id) ON DELETE SET NULL,
    action TEXT NOT NULL,                   -- 'uploaded', 'viewed', 'deleted', 'searched', 'exported'
    target_type TEXT,                       -- 'document', 'organization', 'user'
    target_id UUID,
    metadata JSONB DEFAULT '{}'::jsonb,
    ip_address INET,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_audit_log_user_id ON audit_log(user_id);
CREATE INDEX idx_audit_log_org_id ON audit_log(org_id);
CREATE INDEX idx_audit_log_created_at ON audit_log(created_at);
