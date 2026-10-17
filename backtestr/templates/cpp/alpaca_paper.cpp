// Optional, explicit Alpaca PAPER execution. No live endpoint is compiled in.
#include "strategy.hpp"
#include <curl/curl.h>
#include <nlohmann/json.hpp>
#include <cstdlib>
#include <filesystem>
#include <iostream>
#include <memory>

using Json = nlohmann::json;
using namespace backtestr;
namespace {
constexpr auto kPaperBase = "https://paper-api.alpaca.markets";
constexpr auto kDataBase = "https://data.alpaca.markets";
constexpr double kLimitBuffer = 0.02;
constexpr double kCashReserve = 0.01;

struct CurlGlobal {
    CurlGlobal() { if (curl_global_init(CURL_GLOBAL_DEFAULT) != CURLE_OK) throw std::runtime_error("Cannot initialize HTTPS client"); }
    ~CurlGlobal() { curl_global_cleanup(); }
};
struct Response { long status{}; Json body; };
struct WriteBuffer { std::string text; bool oversized{}; };
std::size_t write_response(char* data, std::size_t size, std::size_t count, void* target) {
    auto& buffer = *static_cast<WriteBuffer*>(target);
    if (size && count > std::numeric_limits<std::size_t>::max() / size) return 0;
    const auto bytes = size * count;
    if (bytes > 32 * 1024 * 1024 || buffer.text.size() > 32 * 1024 * 1024 - bytes) { buffer.oversized = true; return 0; }
    try { buffer.text.append(data, bytes); } catch (...) { return 0; }
    return bytes;
}

class Api {
    std::string key_, secret_;
public:
    Api() {
        const char* key = std::getenv("APCA_API_KEY_ID");
        const char* secret = std::getenv("APCA_API_SECRET_KEY");
        if (!key || !*key) key = std::getenv("ALPACA_API_KEY");
        if (!secret || !*secret) secret = std::getenv("ALPACA_SECRET_KEY");
        if (!key || !secret || !*key || !*secret) throw std::runtime_error("Set APCA_API_KEY_ID and APCA_API_SECRET_KEY to PAPER credentials");
        key_ = key; secret_ = secret;
        if (key_.find_first_of("\r\n") != std::string::npos || secret_.find_first_of("\r\n") != std::string::npos) throw std::runtime_error("Invalid credentials");
    }
    static std::string encoded(const std::string& value) {
        std::unique_ptr<CURL, decltype(&curl_easy_cleanup)> curl(curl_easy_init(), curl_easy_cleanup);
        if (!curl) throw std::runtime_error("Cannot initialize HTTPS client");
        char* result = curl_easy_escape(curl.get(), value.c_str(), static_cast<int>(value.size()));
        if (!result) throw std::runtime_error("Cannot encode API parameter");
        const std::string text(result);
        curl_free(result);
        return text;
    }
    Response request(const char* base, const std::string& path, const Json* payload = nullptr, bool allow_not_found = false) const {
        std::unique_ptr<CURL, decltype(&curl_easy_cleanup)> curl(curl_easy_init(), curl_easy_cleanup);
        if (!curl) throw std::runtime_error("Cannot initialize HTTPS client");
        const std::string url = std::string(base) + path;
        const std::string encoded_body = payload ? payload->dump() : "";
        curl_slist* headers = nullptr;
        headers = curl_slist_append(headers, ("APCA-API-KEY-ID: " + key_).c_str());
        headers = curl_slist_append(headers, ("APCA-API-SECRET-KEY: " + secret_).c_str());
        headers = curl_slist_append(headers, "Content-Type: application/json");
        if (!headers) throw std::runtime_error("Cannot initialize request headers");
        std::unique_ptr<curl_slist, decltype(&curl_slist_free_all)> header_guard(headers, curl_slist_free_all);
        WriteBuffer buffer;
        curl_easy_setopt(curl.get(), CURLOPT_URL, url.c_str());
        curl_easy_setopt(curl.get(), CURLOPT_HTTPHEADER, headers);
        curl_easy_setopt(curl.get(), CURLOPT_USERAGENT, "BacktestR-paper/1.0");
        curl_easy_setopt(curl.get(), CURLOPT_CONNECTTIMEOUT, 10L);
        curl_easy_setopt(curl.get(), CURLOPT_TIMEOUT, 30L);
        curl_easy_setopt(curl.get(), CURLOPT_NOSIGNAL, 1L);
        curl_easy_setopt(curl.get(), CURLOPT_FOLLOWLOCATION, 0L);
        curl_easy_setopt(curl.get(), CURLOPT_SSL_VERIFYPEER, 1L);
        curl_easy_setopt(curl.get(), CURLOPT_SSL_VERIFYHOST, 2L);
        curl_easy_setopt(curl.get(), CURLOPT_WRITEFUNCTION, write_response);
        curl_easy_setopt(curl.get(), CURLOPT_WRITEDATA, &buffer);
        if (payload) {
            curl_easy_setopt(curl.get(), CURLOPT_POST, 1L);
            curl_easy_setopt(curl.get(), CURLOPT_POSTFIELDS, encoded_body.c_str());
            curl_easy_setopt(curl.get(), CURLOPT_POSTFIELDSIZE, static_cast<long>(encoded_body.size()));
        }
        const auto status = curl_easy_perform(curl.get());
        if (status != CURLE_OK) throw std::runtime_error("HTTPS request failed; any order submission may have an uncertain outcome. Reconcile with Alpaca before another run.");
        Response response;
        curl_easy_getinfo(curl.get(), CURLINFO_RESPONSE_CODE, &response.status);
        if (response.status == 404 && allow_not_found) return response;
        if (response.status < 200 || response.status >= 300) throw std::runtime_error("Alpaca API returned HTTP " + std::to_string(response.status) + "; execution stopped");
        response.body = Json::parse(buffer.text);
        return response;
    }
    Json trading(const std::string& path) const { return request(kPaperBase, path).body; }
};

double amount(const Json& value) {
    if (value.is_string()) return number(value.get<std::string>());
    if (value.is_number()) {
        const auto number_value = value.get<double>();
        if (std::isfinite(number_value)) return number_value;
    }
    throw std::runtime_error("Non-finite or missing broker amount");
}

std::string iso_day(Day day) {
    const std::chrono::year_month_day value(day);
    std::ostringstream output;
    output << std::setfill('0') << std::setw(4) << static_cast<int>(value.year()) << '-'
        << std::setw(2) << static_cast<unsigned>(value.month()) << '-' << std::setw(2) << static_cast<unsigned>(value.day());
    return output.str();
}

struct Session { std::string completed, next; Json clock; };
Session session(const Api& api) {
    const auto clock = api.trading("/v2/clock");
    if (clock.at("is_open").get<bool>()) throw std::runtime_error("Run outside market hours to plan the next opening auction");
    const auto timestamp = clock.at("timestamp").get<std::string>();
    if (timestamp.size() < 25 || (!timestamp.ends_with("-04:00") && !timestamp.ends_with("-05:00")))
        throw std::runtime_error("Broker clock must expose America/New_York offset");
    const std::string today = timestamp.substr(0, 10);
    const std::string time = timestamp.substr(11, 5);
    const std::string next = clock.at("next_open").get<std::string>().substr(0, 10);
    const auto calendar = api.trading("/v2/calendar?start=" + iso_day(parse_day(today) - std::chrono::days(60)) + "&end=" + next);
    std::vector<std::string> days;
    for (const auto& item : calendar) days.push_back(item.at("date").get<std::string>());
    std::sort(days.begin(), days.end());
    const auto found = std::find(days.begin(), days.end(), next);
    if (found == days.end() || found == days.begin()) throw std::runtime_error("Cannot establish previous completed exchange session");
    const std::string completed = *std::prev(found);
    if (!((today == completed && time >= "19:00") || (today == next && time < "09:28")))
        throw std::runtime_error("LOO submission window is previous session after 19:00 or next session before 09:28 ET");
    return {completed, next, clock};
}

History fetch_history(const Api& api, const std::string& last_session) {
    std::string symbols;
    for (const auto& symbol : required_symbols()) { if (!symbols.empty()) symbols += ','; symbols += symbol; }
    const std::string path = "/v2/stocks/bars?symbols=" + Api::encoded(symbols) + "&timeframe=1Day&start=" +
        std::string(kHistoryStart) + "T00%3A00%3A00Z&end=" + last_session + "T23%3A59%3A59Z&adjustment=split&feed=iex&sort=asc&limit=10000";
    History result;
    std::string token;
    std::set<std::string> seen;
    bool finished = false;
    for (int page = 0; page < 200; ++page) {
        const auto body = api.request(kDataBase, path + (token.empty() ? "" : "&page_token=" + Api::encoded(token))).body;
        const auto& bars = body.at("bars");
        if (!bars.is_object()) throw std::runtime_error("Missing market history");
        for (const auto& [symbol, rows] : bars.items()) {
            if (!required_symbols().contains(symbol)) throw std::runtime_error("Unrequested symbol returned by market data");
            for (const auto& row : rows) {
                const auto date = row.at("t").get<std::string>().substr(0, 10);
                if (date > last_session) throw std::runtime_error("Future or unfinished daily bar returned");
                result[symbol].push_back({date, amount(row.at("o")), amount(row.at("h")), amount(row.at("l")), amount(row.at("c")), amount(row.at("v"))});
            }
        }
        if (!body.contains("next_page_token") || body.at("next_page_token").is_null()) { finished = true; break; }
        token = body.at("next_page_token").get<std::string>();
        if (token.empty()) { finished = true; break; }
        if (!seen.emplace(token).second) throw std::runtime_error("Market data pagination repeated");
    }
    if (!finished) throw std::runtime_error("Market history pagination limit exceeded");
    validate_history(result);
    for (const auto& [symbol, rows] : result)
        if (rows.size() <= warmup() || rows.back().date != last_session) throw std::runtime_error("Stale or insufficient completed history for " + symbol);
    return result;
}

struct Snapshot { Json account; Holdings holdings; double cash{}, equity{}; };
Snapshot snapshot(const Api& api) {
    Snapshot result;
    result.account = api.trading("/v2/account");
    if (result.account.at("status").get<std::string>() != "ACTIVE" || result.account.value("trading_blocked", true) ||
        result.account.value("account_blocked", true) || result.account.value("trade_suspended_by_user", false))
        throw std::runtime_error("Paper account is not active and permitted to trade");
    result.cash = amount(result.account.at("cash"));
    result.equity = amount(result.account.at("equity"));
    if (result.cash < 0 || result.equity <= 0 || result.cash > result.equity * 1.001) throw std::runtime_error("Unsupported leveraged or invalid account balance");
    const auto orders = api.trading("/v2/orders?status=open&limit=500&nested=true");
    if (!orders.is_array() || !orders.empty()) throw std::runtime_error("Account has open orders; reconcile them before running");
    for (const auto symbol : kSymbols) result.holdings[std::string(symbol)] = 0;
    const auto positions = api.trading("/v2/positions");
    if (!positions.is_array()) throw std::runtime_error("Invalid account positions response");
    for (const auto& position : positions) {
        const auto symbol = position.at("symbol").get<std::string>();
        const double qty = amount(position.at("qty"));
        if (!result.holdings.contains(symbol) || position.at("side").get<std::string>() != "long" || qty != std::floor(qty) || qty < 0)
            throw std::runtime_error("Dedicated paper account required: unknown, short or fractional position found");
        result.holdings[symbol] = quantity(qty);
    }
    return result;
}

std::vector<Json> plan(const History& history, const Snapshot& account, const Session& dates) {
    const auto signals = signal_history(history);
    Prices prices;
    Active active;
    for (const auto symbol_view : kSymbols) {
        const std::string symbol(symbol_view);
        prices[symbol] = history.at(symbol).back().close;
        active[symbol] = signals.at(symbol).back();
    }
    // Research capital is an explicit deployment ceiling, never a license to
    // consume a larger paper account's entire equity.
    const auto target = targets(active, prices, std::min(account.equity, kInitialCash));
    double available = account.cash * (1.0 - kCashReserve);
    std::vector<Json> sells, buys;
    for (const auto& [symbol, desired] : target) {
        const Quantity existing = account.holdings.at(symbol);
        const std::string side = desired < existing ? "sell" : "buy";
        const double raw_price = prices.at(symbol) * (side == "buy" ? 1.0 + kLimitBuffer : 1.0 - kLimitBuffer);
        // US equity limit prices: avoid fractional cents above $1, four decimals below $1.
        const double scale = raw_price >= 1.0 ? 100.0 : 10000.0;
        const double limit = side == "buy" ? std::ceil(raw_price * scale) / scale : std::floor(raw_price * scale) / scale;
        if (limit <= 0) throw std::runtime_error("Price too small for supported broker tick size");
        Quantity shares = desired < existing ? existing - desired : desired - existing;
        if (side == "buy") {
            shares = std::min(shares, quantity(std::max(0.0, available) / (limit * (1.0 + kCommissionBps / 10000.0))));
            available -= static_cast<double>(shares) * limit * (1.0 + kCommissionBps / 10000.0);
        }
        if (!shares) continue;
        const std::string id = "btr-" + std::string(kStrategyId) + '-' + dates.next + '-' + symbol + (side == "buy" ? "-b" : "-s");
        if (id.size() > 48) throw std::runtime_error("Client order ID exceeds broker limit");
        std::ostringstream limit_text;
        limit_text << std::fixed << std::setprecision(raw_price >= 1.0 ? 2 : 4) << limit;
        Json order = {{"symbol", symbol}, {"qty", std::to_string(shares)}, {"side", side}, {"type", "limit"},
            {"limit_price", limit_text.str()}, {"time_in_force", "opg"}, {"client_order_id", id}, {"extended_hours", false}};
        (side == "sell" ? sells : buys).push_back(std::move(order));
    }
    sells.insert(sells.end(), buys.begin(), buys.end());
    return sells;
}
} // namespace

int main(int argc, char** argv) {
    bool submit = false;
    std::string state_directory = "paper-state";
    try {
        for (int i = 1; i < argc; ++i) {
            if (std::string(argv[i]) == "--submit-paper") submit = true;
            else if (std::string(argv[i]) == "--state-dir" && i + 1 < argc) state_directory = argv[++i];
            else throw std::runtime_error("Usage: backtestr_paper [--submit-paper] [--state-dir persistent-directory]");
        }
        CurlGlobal runtime;
        const Api api;
        const auto dates = session(api);
        if (!rebalance_due(dates.completed, dates.next)) {
            std::cout << "No rebalance due for " << dates.next << '\n';
            return 0;
        }
        const auto history = fetch_history(api, dates.completed);
        const auto account = snapshot(api);
        for (const auto symbol : kSymbols) {
            const auto asset = api.trading("/v2/assets/" + Api::encoded(std::string(symbol)));
            if (!asset.value("tradable", false) || asset.value("status", "") != "active" || asset.value("class", "") != "us_equity")
                throw std::runtime_error("Universe contains a non-tradable US equity");
        }
        const auto orders = plan(history, account, dates);
        for (const auto& order : orders) {
            const auto previous = api.request(kPaperBase, "/v2/orders:by_client_order_id?client_order_id=" + Api::encoded(order.at("client_order_id").get<std::string>()), nullptr, true);
            if (previous.status != 404) throw std::runtime_error("An order for this strategy/session already exists; reconcile instead of submitting again");
        }
        Json output = {{"mode", submit ? "paper-submit" : "paper-preview"}, {"strategy_id", kStrategyId},
            {"signal_date", dates.completed}, {"auction_date", dates.next}, {"orders", orders},
            {"cash_used_without_sale_proceeds", true}, {"buy_limit_buffer", kLimitBuffer}, {"cash_reserve", kCashReserve}};
        std::cout << output.dump(2) << '\n';
        if (!submit || orders.empty()) return 0;
        // Re-read the account immediately before claiming this persistent run.
        const auto fresh = snapshot(api);
        if (fresh.holdings != account.holdings || std::abs(fresh.cash - account.cash) > 0.01)
            throw std::runtime_error("Account changed during preflight; execution stopped");
        const auto fresh_dates = session(api);
        if (fresh_dates.completed != dates.completed || fresh_dates.next != dates.next) throw std::runtime_error("Broker session changed during preflight");
        std::filesystem::create_directories(state_directory);
        const auto claim = std::filesystem::path(state_directory) / (std::string(kStrategyId) + '-' + dates.next);
        if (!std::filesystem::create_directory(claim)) throw std::runtime_error("Persistent strategy/session run already exists; inspect its journal and Alpaca orders");
        std::ofstream journal(claim / "journal.jsonl", std::ios::app);
        if (!journal) throw std::runtime_error("Cannot create submission journal");
        journal << output.dump() << '\n' << std::flush;
        if (!journal) throw std::runtime_error("Cannot persist submission plan");
        for (const auto& order : orders) {
            // Intent is flushed before POST. An ambiguous request stops the process; it is never retried.
            journal << Json({{"intent", order}}).dump() << '\n' << std::flush;
            if (!journal) throw std::runtime_error("Cannot persist order intent");
            const auto response = api.request(kPaperBase, "/v2/orders", &order).body;
            journal << Json({{"accepted_id", response.at("id")}, {"client_order_id", order.at("client_order_id")}, {"status", response.at("status")}}).dump() << '\n' << std::flush;
            if (!journal) throw std::runtime_error("Cannot persist order acknowledgement; reconcile with broker");
            const std::string status = response.at("status").get<std::string>();
            if (status == "rejected" || status == "canceled" || status == "expired" || status == "suspended")
                throw std::runtime_error("Broker did not accept an order; partial plan requires manual reconciliation");
        }
        std::cout << "Paper orders submitted. Monitor auction fills and cancellations in Alpaca.\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "BacktestR PAPER stopped: " << error.what() << '\n';
        return 1;
    }
}
