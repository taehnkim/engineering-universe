package prepare

import (
	"reflect"
	"strings"
	"testing"
)

func TestPreparePreservesPreCleanupIDs(t *testing.T) {
	raw := `<html><head><title>Ignored</title></head><body><nav>Menu</nav><main><h1>Title</h1><p>Body</p></main></body></html>`
	got, err := Prepare(raw)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(got.NodeIDs, []int{0, 1, 3, 4, 5}) {
		t.Fatalf("surviving IDs = %v", got.NodeIDs)
	}
	if strings.Contains(got.CleanHTML, "Menu") || strings.Contains(got.CleanHTML, "Ignored") {
		t.Fatalf("chrome survived: %s", got.CleanHTML)
	}
	if !strings.Contains(got.PreviewHTML, `data-eu-node-id="4"`) {
		t.Fatalf("preview lost title ID: %s", got.PreviewHTML)
	}
}

func TestPrepareRemovesHiddenAndMedia(t *testing.T) {
	raw := `<main><h1>Title</h1><img src="x"><div class="SiteFooter">Footer</div><p aria-hidden="true">Hidden</p><p>Body</p></main>`
	got, err := Prepare(raw)
	if err != nil {
		t.Fatal(err)
	}
	for _, unwanted := range []string{"<img", "Footer", "Hidden"} {
		if strings.Contains(got.CleanHTML, unwanted) {
			t.Fatalf("%q survived: %s", unwanted, got.CleanHTML)
		}
	}
	if !strings.Contains(got.CleanHTML, "Body") {
		t.Fatalf("article body removed: %s", got.CleanHTML)
	}
}
