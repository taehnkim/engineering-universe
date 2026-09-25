package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"time"

	"enguniverse/domprep/prepare"
)

type manifest struct {
	Pages []struct {
		HTMLPath string `json:"html_path"`
	} `json:"pages"`
}

func percentile(times []float64, fraction float64) float64 {
	index := int(float64(len(times))*fraction+.999999) - 1
	return times[index]
}

func main() {
	if len(os.Args) != 2 {
		panic("usage: go run ./cmd/bench DATASET_DIR")
	}
	root := os.Args[1]
	data, err := os.ReadFile(filepath.Join(root, "manifest.json"))
	if err != nil {
		panic(err)
	}
	var m manifest
	if err := json.Unmarshal(data, &m); err != nil {
		panic(err)
	}
	pages := make([]string, 0, len(m.Pages))
	for _, page := range m.Pages {
		data, err := os.ReadFile(filepath.Join(root, page.HTMLPath))
		if err != nil {
			panic(err)
		}
		pages = append(pages, string(data))
	}
	if len(pages) == 0 {
		panic("manifest has no pages")
	}
	warmCount := len(pages)
	if warmCount > 10 {
		warmCount = 10
	}
	for _, page := range pages[:warmCount] {
		if _, err := prepare.Prepare(page); err != nil {
			panic(err)
		}
	}
	times := make([]float64, 0, len(pages)*3)
	for round := 0; round < 3; round++ {
		for _, page := range pages {
			started := time.Now()
			if _, err := prepare.Prepare(page); err != nil {
				panic(err)
			}
			times = append(times, float64(time.Since(started).Nanoseconds())/1e6)
		}
	}
	sort.Float64s(times)
	fmt.Printf("pages=%d runs=%d median=%.3fms p95=%.3fms\n",
		len(pages), len(times), percentile(times, .5), percentile(times, .95))
}
