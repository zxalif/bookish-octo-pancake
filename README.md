# FreelanceHunt Backend API

**FastAPI Backend for FreelanceHunt - Reddit-First Lead Generation Platform for Freelancers**

---

## 🚀 Quick Start

### Paddle Setup (Required First)

Before running the application, set up Paddle products and prices:

```bash
# 1. Set Paddle credentials in .env
PADDLE_API_KEY=your_api_key
PADDLE_VENDOR_ID=your_vendor_id
PADDLE_ENVIRONMENT=sandbox

# 2. Run the setup script
python scripts/setup_paddle_products.py

# 3. Copy price IDs from .env.paddle to .env
```

See [docs/guides/PADDLE_SETUP.md](docs/guides/PADDLE_SETUP.md) for detailed instructions.

### With Docker (Recommended)
```bash
# 1. Copy environment file
cp .env.example .env

# 2. Start services
docker-compose up --build

# 3. Run migrations
docker-compose exec lead-api alembic upgrade head

# 4. Access API
# - API: http://localhost:7300
# - Docs: http://localhost:7300/docs
# - Health: http://localhost:7300/health
```

### Local Development
```bash
# 1. Create virtual environment
python3.11 -m venv venv
source venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Setup environment
cp .env.example .env
# Edit .env with your configuration

# 4. Run migrations
alembic upgrade head

# 5. Start server
uvicorn api.main:app --reload --port 7300
```

---

## 📚 Documentation

- **[Setup Guide](docs/SETUP.md)** - Complete setup instructions
- **[Authentication Guide](docs/AUTHENTICATION.md)** - Auth flow and JWT tokens
- **[Backend Status](docs/status/BACKEND_STATUS.md)** - Current implementation status
- **[Backend Plan](docs/planning/BACKEND_PLAN.md)** - Complete implementation plan
- **[Documentation Index](docs/README.md)** - Complete documentation structure
- **[Completion Summary](docs/status/COMPLETION_SUMMARY.md)** - Progress breakdown

---

## 🎯 Features

- ✅ **Authentication** - JWT-based auth with password hashing
- ✅ **User Management** - Profile management with subscription data
- ✅ **Subscriptions** - Plan management (Starter, Professional, Power)
- ✅ **Payments** - Paddle integration for subscriptions
- ✅ **Usage Tracking** - Monitor API usage and enforce limits
- ✅ **Keyword Searches** - Manage keyword searches (concurrent limits)
- ✅ **Opportunities** - Generate and manage opportunities from Reddit
- ✅ **Multi-tenancy** - User isolation and data scoping

---

## 📊 Current Status

**Overall Completion**: **95%** ✅

- **Core Features**: 100% ✅
- **Infrastructure**: 100% ✅
- **Documentation**: 40% ⏳
- **Testing**: 10% ⏳

See [Completion Summary](docs/status/COMPLETION_SUMMARY.md) for detailed breakdown.

---

## 🔧 Configuration

All configuration is done via environment variables. See `.env.example` for all available options.

**Required for development:**
- `DATABASE_URL` - PostgreSQL connection string
- `SECRET_KEY` - JWT secret key
- `REDIS_URL` - Redis connection string

**For production:**
- All variables should be set
- Use secure `SECRET_KEY`
- Configure Paddle credentials
- Configure SMTP for emails

---

## 🗄️ Database

- **PostgreSQL 16+** (production)
- **SQLite** (development/testing)
- **Migrations**: Alembic

### Run Migrations
```bash
# Create migration
alembic revision --autogenerate -m "Description"

# Apply migrations
alembic upgrade head

# Rollback
alembic downgrade -1
```

---

## 🧪 Testing

Test structure is set up. Run tests with:
```bash
pytest
```

---

## 📡 API Endpoints

### Authentication
- `POST /api/v1/auth/register` - Register new user
- `POST /api/v1/auth/login` - Login user
- `GET /api/v1/auth/me` - Get current user
- `POST /api/v1/auth/forgot-password` - Request password reset
- `POST /api/v1/auth/reset-password` - Reset password

### Users
- `GET /api/v1/users/me` - Get current user (with subscription)
- `PUT /api/v1/users/me` - Update profile
- `DELETE /api/v1/users/me` - Deactivate account

### Subscriptions
- `GET /api/v1/subscriptions/current` - Get active subscription
- `POST /api/v1/subscriptions/create` - Create subscription
- `POST /api/v1/subscriptions/cancel` - Cancel subscription
- `GET /api/v1/subscriptions/history` - Subscription history
- `GET /api/v1/subscriptions/limits` - Plan limits

### Opportunities
- `GET /api/v1/opportunities` - List opportunities
- `POST /api/v1/opportunities/generate` - Generate opportunities
- `GET /api/v1/opportunities/{id}` - Get opportunity
- `PUT /api/v1/opportunities/{id}` - Update opportunity
- `DELETE /api/v1/opportunities/{id}` - Delete opportunity

**Full API Documentation**: http://localhost:7300/docs

---

## 🐳 Docker

### Services
- **lead-api** - Backend API (port 7300)
- **postgres** - PostgreSQL database (port 5432)
- **redis** - Redis cache (port 6379)

### Commands
```bash
# Start services
docker-compose up

# Start in background
docker-compose up -d

# View logs
docker-compose logs -f lead-api

# Stop services
docker-compose down

# Rebuild
docker-compose up --build
```

---

## 🔗 Integration

### zola-lead API
The backend integrates with `zola-lead` API (microservices architecture):
- `zola-lead` runs on port 7100
- Configure `ZOLA_LEAD_API_URL` and `ZOLA_LEAD_API_KEY` in `.env`

### Paddle Payments
Payment processing via Paddle:
- Configure `PADDLE_API_KEY`, `PADDLE_VENDOR_ID` in `.env`
- Set up webhook endpoint for subscription events

---

## 📝 License

See project root for license information.

---

## 🤝 Contributing

See project root for contributing guidelines.

---

**Status**: Production-ready for core features! 🚀
