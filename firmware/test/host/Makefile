# firmware/test/host/Makefile
CXXFLAGS = -std=c++11 -Wall -Wextra -Werror -O1

all: layout_vectors inflight_test

layout_vectors: layout_vectors.cpp ../../libraries/GreenhouseMesh/mesh_packet.h
	$(CXX) $(CXXFLAGS) -o $@ layout_vectors.cpp

inflight_test: inflight_test.cpp ../../libraries/GreenhouseMesh/mesh_inflight.h ../../libraries/GreenhouseMesh/mesh_packet.h
	$(CXX) $(CXXFLAGS) -o $@ inflight_test.cpp

.PHONY: all clean
clean:
	rm -f layout_vectors inflight_test
