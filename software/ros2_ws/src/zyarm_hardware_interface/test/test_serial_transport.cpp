#include "zyarm_hardware_interface/serial_transport.hpp"

#include <chrono>
#include <condition_variable>
#include <deque>
#include <mutex>
#include <string>
#include <vector>

#include "gtest/gtest.h"

namespace zyarm_hardware_interface
{

namespace
{
using namespace std::chrono_literals;

class FakeLineIo : public LineIo
{
public:
  bool open(const SerialConfig &, std::string *) override
  {
    std::lock_guard<std::mutex> lock(mutex_);
    open_ = true;
    return true;
  }

  void close() override
  {
    {
      std::lock_guard<std::mutex> lock(mutex_);
      open_ = false;
    }
    cv_.notify_all();
  }

  bool is_open() const override
  {
    std::lock_guard<std::mutex> lock(mutex_);
    return open_;
  }

  bool write_line(const std::string & line, std::string * error) override
  {
    std::lock_guard<std::mutex> lock(mutex_);
    if (!write_ok_) {
      if (error != nullptr) {
        *error = "fake write failed";
      }
      return false;
    }
    writes_.push_back(line);
    return true;
  }

  bool read_line(std::string & line, std::chrono::milliseconds timeout, std::string *) override
  {
    std::unique_lock<std::mutex> lock(mutex_);
    if (!cv_.wait_for(lock, timeout, [&]() {return !read_lines_.empty() || !open_;})) {
      return false;
    }
    if (read_lines_.empty()) {
      return false;
    }
    line = read_lines_.front();
    read_lines_.pop_front();
    return true;
  }

  void push_line(const std::string & line)
  {
    {
      std::lock_guard<std::mutex> lock(mutex_);
      read_lines_.push_back(line);
    }
    cv_.notify_all();
  }

  void set_write_ok(bool ok)
  {
    std::lock_guard<std::mutex> lock(mutex_);
    write_ok_ = ok;
  }

  std::vector<std::string> writes() const
  {
    std::lock_guard<std::mutex> lock(mutex_);
    return writes_;
  }

private:
  mutable std::mutex mutex_;
  std::condition_variable cv_;
  std::deque<std::string> read_lines_;
  std::vector<std::string> writes_;
  bool open_{false};
  bool write_ok_{true};
};

SerialConfig fast_config()
{
  SerialConfig config;
  config.read_timeout = 5ms;
  config.write_timeout = 5ms;
  return config;
}
}  // namespace

TEST(SerialTransport, ReceivesValidStatusOnBackgroundThread)
{
  auto fake_io = std::make_unique<FakeLineIo>();
  auto * fake = fake_io.get();
  SerialTransport transport(std::move(fake_io));

  std::string error;
  ASSERT_TRUE(transport.open(fast_config(), &error)) << error;
  fake->push_line("[STATUS] J0:1 J1:2 J2:3 J3:4 J4:5 J5:6 CLAW:7");

  const auto frame = transport.wait_for_status_after({}, 100ms);
  ASSERT_TRUE(frame.has_value());
  EXPECT_DOUBLE_EQ(frame->hardware_positions[0], 1.0);
  EXPECT_DOUBLE_EQ(frame->hardware_positions[6], 7.0);

  transport.close();
  EXPECT_FALSE(fake->is_open());
}

TEST(SerialTransport, IgnoresMalformedStatusAndKeepsWaiting)
{
  auto fake_io = std::make_unique<FakeLineIo>();
  auto * fake = fake_io.get();
  SerialTransport transport(std::move(fake_io));

  std::string error;
  ASSERT_TRUE(transport.open(fast_config(), &error)) << error;
  fake->push_line("[STATUS] J0:bad J1:2 J2:3 J3:4 J4:5 J5:6 CLAW:7");
  EXPECT_FALSE(transport.wait_for_status_after({}, 20ms).has_value());

  fake->push_line("[STATUS] J0:10 J1:20 J2:30 J3:40 J4:50 J5:60 CLAW:70");
  const auto frame = transport.wait_for_status_after({}, 100ms);
  ASSERT_TRUE(frame.has_value());
  EXPECT_DOUBLE_EQ(frame->hardware_positions[0], 10.0);
  EXPECT_DOUBLE_EQ(frame->hardware_positions[6], 70.0);

  transport.close();
}

TEST(SerialTransport, WriteLineDoesNotWaitForStatus)
{
  auto fake_io = std::make_unique<FakeLineIo>();
  auto * fake = fake_io.get();
  SerialTransport transport(std::move(fake_io));

  std::string error;
  ASSERT_TRUE(transport.open(fast_config(), &error)) << error;
  ASSERT_TRUE(transport.write_line("[CMD][36][0 0 0 0 0 0 0]\n", &error)) << error;

  EXPECT_EQ(fake->writes().size(), 1u);
  EXPECT_FALSE(transport.latest_status().has_value());

  transport.close();
}

TEST(SerialTransport, ReceivesCompletedAckOnBackgroundThread)
{
  auto fake_io = std::make_unique<FakeLineIo>();
  auto * fake = fake_io.get();
  SerialTransport transport(std::move(fake_io));

  std::string error;
  ASSERT_TRUE(transport.open(fast_config(), &error)) << error;
  const auto baseline = std::chrono::steady_clock::now();
  fake->push_line("ACK_COMPLETED: CMD_ID=38, SUCCESS");

  const auto ack = transport.wait_for_ack_after(38, baseline, 100ms);
  ASSERT_TRUE(ack.has_value());
  EXPECT_TRUE(ack->success);
  EXPECT_EQ(ack->command_id, 38);
  transport.close();
}

TEST(SerialTransport, ReceivesAndSequencesServoTemperatures)
{
  auto fake_io = std::make_unique<FakeLineIo>();
  auto * fake = fake_io.get();
  SerialTransport transport(std::move(fake_io));

  std::string error;
  ASSERT_TRUE(transport.open(fast_config(), &error)) << error;
  fake->push_line("[SERVO_TEMP] S1:32 S2:30 S9:29");

  const auto first = transport.wait_for_servo_temperatures_after(0, 100ms);
  ASSERT_TRUE(first.has_value());
  EXPECT_EQ(first->sequence, 1u);
  EXPECT_DOUBLE_EQ(first->temperatures_c.at(1), 32.0);

  fake->push_line("[SERVO_TEMP] S1:33 S2:31 S9:30");
  const auto second = transport.wait_for_servo_temperatures_after(first->sequence, 100ms);
  ASSERT_TRUE(second.has_value());
  EXPECT_EQ(second->sequence, 2u);
  EXPECT_DOUBLE_EQ(second->temperatures_c.at(9), 30.0);
  transport.close();
}

TEST(SerialTransport, ReportsWriteFailureAndClosesCleanly)
{
  auto fake_io = std::make_unique<FakeLineIo>();
  auto * fake = fake_io.get();
  SerialTransport transport(std::move(fake_io));

  std::string error;
  ASSERT_TRUE(transport.open(fast_config(), &error)) << error;
  fake->set_write_ok(false);

  EXPECT_FALSE(transport.write_line("[CMD][36][0 0 0 0 0 0 0]\n", &error));
  EXPECT_EQ(error, "fake write failed");

  transport.close();
  EXPECT_FALSE(fake->is_open());
}

}  // namespace zyarm_hardware_interface
