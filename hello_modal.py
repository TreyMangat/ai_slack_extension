import modal
app = modal.App("hello-modal")

@app.function()
def hello():
    return "Modal is working ✅"

if __name__ == "__main__":
    with app.run():
        print(hello.remote())