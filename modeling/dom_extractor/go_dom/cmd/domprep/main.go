package main

import (
	"encoding/json"
	"io"
	"log"
	"os"

	"enguniverse/domprep/prepare"
)

func main() {
	raw, err := io.ReadAll(os.Stdin)
	if err != nil {
		log.Fatal(err)
	}
	result, err := prepare.Prepare(string(raw))
	if err != nil {
		log.Fatal(err)
	}
	if err := json.NewEncoder(os.Stdout).Encode(result); err != nil {
		log.Fatal(err)
	}
}
