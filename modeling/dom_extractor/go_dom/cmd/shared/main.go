package main

/*
#include <stdlib.h>
*/
import "C"

import (
	"encoding/json"
	"unsafe"

	"enguniverse/domprep/prepare"
)

//export PrepareHTML
func PrepareHTML(input *C.char, length C.int) *C.char {
	if length < 0 {
		return nil
	}
	raw := C.GoBytes(unsafe.Pointer(input), length)
	prepared, err := prepare.Prepare(string(raw))
	if err != nil {
		return nil
	}
	encoded, err := json.Marshal(prepared)
	if err != nil {
		return nil
	}
	return C.CString(string(encoded))
}

//export FreePreparedHTML
func FreePreparedHTML(result *C.char) {
	C.free(unsafe.Pointer(result))
}

func main() {}
