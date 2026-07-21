#include "zyarm_hardware_interface/shell_protocol.hpp"

#include <cmath>
#include <iomanip>
#include <regex>
#include <sstream>

namespace zyarm_hardware_interface
{

namespace
{
const std::regex kStatusRegex(
  R"(\[STATUS\]\s*J0:([-\d.]+)\s*J1:([-\d.]+)\s*J2:([-\d.]+)\s*J3:([-\d.]+)\s*J4:([-\d.]+)\s*J5:([-\d.]+)\s*CLAW:([-\d.]+))");
const std::regex kCompletedAckRegex(
  R"(ACK_COMPLETED:\s*CMD_ID=(\d+),\s*(SUCCESS|ERROR))");
const std::regex kServoTemperatureFieldRegex(R"(S(\d+):([-+]?\d+(?:\.\d+)?))");

std::string format_number(double value)
{
  const double rounded = std::round(value);
  if (std::abs(value - rounded) < 1e-9) {
    return std::to_string(static_cast<long long>(rounded));
  }

  std::ostringstream stream;
  stream << std::fixed << std::setprecision(3) << value;
  return stream.str();
}
}  // namespace

std::string format_command(int command_id, const std::vector<double> & params)
{
  std::ostringstream stream;
  stream << "[CMD][" << command_id << "]";
  if (!params.empty()) {
    stream << "[";
    for (std::size_t index = 0; index < params.size(); ++index) {
      if (index > 0) {
        stream << " ";
      }
      stream << format_number(params[index]);
    }
    stream << "]";
  }
  stream << "\n";
  return stream.str();
}

std::string format_joint_io_fast_command(const std::array<double, kJointCount> & hardware_positions)
{
  return format_command(
    kJointIoFastCommandId,
    std::vector<double>(hardware_positions.begin(), hardware_positions.end()));
}

std::optional<AckFrame> parse_completed_ack(
  const std::string & line, std::chrono::steady_clock::time_point received_at)
{
  std::smatch match;
  if (!std::regex_search(line, match, kCompletedAckRegex) || match.size() != 3) {
    return std::nullopt;
  }

  AckFrame frame;
  try {
    frame.command_id = std::stoi(match[1].str());
  } catch (const std::exception &) {
    return std::nullopt;
  }
  frame.success = match[2].str() == "SUCCESS";
  frame.received_at = received_at;
  frame.raw_line = line;
  return frame;
}

std::optional<std::array<double, kJointCount>> parse_status_values(const std::string & line)
{
  std::smatch match;
  if (!std::regex_search(line, match, kStatusRegex) || match.size() != kJointCount + 1) {
    return std::nullopt;
  }

  std::array<double, kJointCount> values{};
  try {
    for (std::size_t index = 0; index < kJointCount; ++index) {
      const auto field = match[index + 1].str();
      std::size_t parsed = 0;
      values[index] = std::stod(field, &parsed);
      if (parsed != field.size()) {
        return std::nullopt;
      }
    }
  } catch (const std::exception &) {
    return std::nullopt;
  }
  return values;
}

std::optional<StatusFrame> parse_status_frame(
  const std::string & line, std::chrono::steady_clock::time_point received_at)
{
  auto values = parse_status_values(line);
  if (!values.has_value()) {
    return std::nullopt;
  }
  StatusFrame frame;
  frame.hardware_positions = *values;
  frame.received_at = received_at;
  frame.raw_line = line;
  return frame;
}

std::optional<ServoTemperatureFrame> parse_servo_temperature_line(
  const std::string & line,
  std::uint64_t sequence,
  std::chrono::steady_clock::time_point received_at)
{
  constexpr const char * marker = "[SERVO_TEMP]";
  const auto marker_index = line.find(marker);
  if (marker_index == std::string::npos) {
    return std::nullopt;
  }

  const auto payload = line.substr(marker_index + std::char_traits<char>::length(marker));
  std::map<int, double> temperatures;
  for (
    auto iter = std::sregex_iterator(
      payload.begin(), payload.end(), kServoTemperatureFieldRegex);
    iter != std::sregex_iterator(); ++iter)
  {
    try {
      const int servo_id = std::stoi((*iter)[1].str());
      const double temperature_c = std::stod((*iter)[2].str());
      if (servo_id > 0) {
        temperatures[servo_id] = temperature_c;
      }
    } catch (const std::exception &) {
      return std::nullopt;
    }
  }
  if (temperatures.empty()) {
    return std::nullopt;
  }

  ServoTemperatureFrame frame;
  frame.temperatures_c = std::move(temperatures);
  frame.received_at = received_at;
  frame.sequence = sequence;
  frame.raw_line = line;
  return frame;
}

}  // namespace zyarm_hardware_interface
