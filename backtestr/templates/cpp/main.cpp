#include "strategy.hpp"
#include <iostream>

int main(int argc, char** argv) {
    using namespace backtestr;
    try {
        if (argc < 2) {
            std::cerr << "Usage: backtestr_replay bars.csv [--trades trades.csv] [--equity equity.csv]\n";
            return 2;
        }
        std::string trade_path, equity_path;
        for (int i = 2; i < argc; i += 2) {
            if (i + 1 >= argc) throw std::runtime_error("Missing option value");
            if (std::string(argv[i]) == "--trades") trade_path = argv[i + 1];
            else if (std::string(argv[i]) == "--equity") equity_path = argv[i + 1];
            else throw std::runtime_error("Unknown option");
        }
        std::ifstream input(argv[1]);
        if (!input) throw std::runtime_error("Cannot open input CSV");
        const auto result = replay(read_csv(input));
        if (!trade_path.empty()) {
            std::ofstream output(trade_path);
            if (!output) throw std::runtime_error("Cannot open trades output");
            output << "date,signal_date,symbol,side,shares,price,commission\n" << std::setprecision(17);
            for (const auto& trade : result.trades)
                output << trade.date << ',' << trade.signal_date << ',' << trade.symbol << ',' << trade.side << ',' << trade.shares << ',' << trade.price << ',' << trade.commission << '\n';
            if (!output) throw std::runtime_error("Failed writing trades output");
        }
        if (!equity_path.empty()) {
            std::ofstream output(equity_path);
            if (!output) throw std::runtime_error("Cannot open equity output");
            output << "date,equity,cash\n" << std::setprecision(17);
            for (const auto& point : result.equity) output << point.date << ',' << point.equity << ',' << point.cash << '\n';
            if (!output) throw std::runtime_error("Failed writing equity output");
        }
        std::cout << std::setprecision(17) << "{\"final_equity\":" << result.equity.back().equity
            << ",\"total_return\":" << result.equity.back().equity / kInitialCash - 1.0
            << ",\"cash\":" << result.cash << ",\"trade_count\":" << result.trades.size() << ",\"positions\":{";
        bool first = true;
        for (const auto& [symbol, shares] : result.positions) {
            if (!first) std::cout << ',';
            first = false;
            std::cout << '"' << symbol << "\":" << shares;
        }
        std::cout << "}}\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "BacktestR: " << error.what() << '\n';
        return 1;
    }
}
