#include "strategy.hpp"
#include <iostream>

int main() {
    using namespace backtestr;
    try {
        bool rejected = false;
        try { parse_day("2024-02-30"); } catch (...) { rejected = true; }
        if (!rejected) throw std::runtime_error("Impossible calendar date accepted");
        rejected = false;
        try { number("nan"); } catch (...) { rejected = true; }
        if (!rejected) throw std::runtime_error("NaN accepted");
        rejected = false;
        try { quantity(1e20); } catch (...) { rejected = true; }
        if (!rejected) throw std::runtime_error("Overflowing share quantity accepted");
        if (precise_sum({1e16, 1.0, -1e16}) != 1.0) throw std::runtime_error("Portfolio sum lost a low-order value");
        Active active;
        Prices prices;
        for (const auto symbol : kSymbols) { active[std::string(symbol)] = true; prices[std::string(symbol)] = 100; }
        const auto target = targets(active, prices, 1000);
        double allocated{};
        for (const auto& [symbol, shares] : target) {
            if (shares < 0 || static_cast<double>(shares) * 100 > 1000 * kMaxPositionWeight + 1e-8)
                throw std::runtime_error("Per-position weight cap violated");
            allocated += static_cast<double>(shares) * prices.at(symbol);
        }
        if (allocated > 1000 + 1e-8) throw std::runtime_error("Portfolio cash allocation exceeded");
        std::cout << "Core safety tests passed\n";
        return 0;
    } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
