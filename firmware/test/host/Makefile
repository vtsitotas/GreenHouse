# firmware/test/host/Makefile
CXXFLAGS = -std=c++11 -Wall -Wextra -Werror -O1

all: layout_vectors inflight_test sched_tests framing_tests sht4x_tests

layout_vectors: layout_vectors.cpp ../../libraries/GreenhouseMesh/mesh_packet.h
	$(CXX) $(CXXFLAGS) -o $@ layout_vectors.cpp

inflight_test: inflight_test.cpp ../../libraries/GreenhouseMesh/mesh_inflight.h ../../libraries/GreenhouseMesh/mesh_packet.h
	$(CXX) $(CXXFLAGS) -o $@ inflight_test.cpp

sched_tests: sched_tests.cpp ../../libraries/GreenhouseMesh/mesh_sched.h ../../libraries/GreenhouseMesh/mesh_config.h
	$(CXX) $(CXXFLAGS) -o $@ sched_tests.cpp

framing_tests: framing_tests.cpp ../../libraries/GreenhouseMesh/mesh_uart.h
	$(CXX) $(CXXFLAGS) -o $@ framing_tests.cpp

sht4x_tests: sht4x_tests.cpp ../../edge_node_esp32_c3/sht4x.h
	$(CXX) $(CXXFLAGS) -o $@ sht4x_tests.cpp

.PHONY: all clean
clean:
	rm -f layout_vectors inflight_test sched_tests framing_tests sht4x_tests
