#include <atomic>
#include <chrono>
#include <ctime>
#include <deque>
#include <functional>
#include <iomanip>
#include <iostream>
#include <string>
#include <tuple>

#include <cassert>
#include <fcntl.h>
#include <sys/epoll.h>
#include <sys/socket.h>
#include <unistd.h>

#include "logging.hpp"

namespace io
{
    class selectable: public std::enable_shared_from_this<selectable>
    {
        public:
            virtual int fd() = 0;
            virtual void do_read() = 0;
            virtual void do_write() = 0;
            virtual ~selectable() {};

            void do_close();
    };

    typedef std::tuple<std::chrono::milliseconds, std::function<void()>> delayed_call;

    class loop
    {
        std::map<int, std::shared_ptr<io::selectable>> selectables;

        std::deque<delayed_call> delayed_calls;

        int epoll_fd;
        std::atomic<bool> running;

        public:
        loop() : epoll_fd(-1), running(false) {};
        ~loop();

        void init();
        void add_selectable(std::shared_ptr<io::selectable> selectable);
        void remove_selectable(int fd);
        void call_soon(std::function<void()> func);
        void call_later(std::chrono::milliseconds delay, std::function<void()> func);
        void stop();

        static loop* instance() {
            static loop ev;
            return &ev;
        };

        void run();
        void run_delayed_calls(const std::chrono::milliseconds &elapsed);
    };

    std::string error(int errnum)
    {
        std::string msg;
        char buf[512];
        msg = strerror_r(errnum, buf, 512);
        return msg + " (" + std::to_string(errnum) + ")";
    }

    std::string write(int fd, std::string buf)
    {
        int written = ::write(fd, buf.c_str(), buf.size());

        if (written < 0) {
            int xerrno = errno;
            logger(2, "write error " << io::error(xerrno));
            std::cout << "CLOSED CLOSE WOOOO OOOOOO" << std::endl;
        } else {
            buf = buf.substr(written, std::string::npos);
        }

        return buf;
    }

    std::string consume(int fd)
    {
        std::string msg;
        ssize_t rsize;
        char buf[512];

        while (true) {
            memset(&buf, 0, 512);
            rsize = read(fd, buf, sizeof(buf));
            logger(5, "read " << rsize << " bytes from " << fd);
            if (rsize == -1) {
                if (errno != EAGAIN) {
                    perror ("read");
                    // FIXME: raise exception. Would be cool to make sure if
                    // msg.size() that we... handle that in some way.
                }
                break;
            } else if (rsize == 0) {
                break;
            } else {
                msg += buf;
            }
        }
        return msg;
    }

    void nonblocking(int fd)
    {
        int flags;

        flags = fcntl (fd, F_GETFL, 0);
        if (flags == -1) {
            int xerrno = errno;
            logger(1, "failed to get fcntl flags for FD " << fd << io::error(xerrno));
            return;
        }

        flags |= O_NONBLOCK;
        if (-1 == fcntl(fd, F_SETFL, flags)) {
            int xerrno = errno;
            logger(1, "failed to set nonblocking for FD " << fd << io::error(xerrno));
            return;
        }
    };
}

void io::selectable::do_close()
{
    logger(3, "closing FD " << fd());
    io::loop::instance()->remove_selectable(fd());
    close(fd());
}

io::loop::~loop()
{
    logger(4, "loop destructed with " << delayed_calls.size() << " delayed calls remaining");
}

void io::loop::init()
{
    if ((this->epoll_fd = epoll_create1(0)) < 0) {
        int xerrno = errno;
        logger(1, "epoll_create error " << io::error(xerrno));
        return;
    }

    logger(4, "epoll fd created " << this->epoll_fd);

    running = true;
}

void io::loop::add_selectable(std::shared_ptr<io::selectable> selectable)
{
    assert(selectable);

    const int fd = selectable->fd();

    epoll_event ev;

    ev.data.fd = fd;
    ev.events = EPOLLIN | EPOLLET | EPOLLOUT;

    assert(this->epoll_fd != -1);

    selectables[fd] = selectable;

    if (epoll_ctl(this->epoll_fd, EPOLL_CTL_ADD, fd, &ev) < 0) {
        int xerrno = errno;
        logger(1, "epoll_ctl EPOLL_CTL_ADD failed for FD " << fd << io::error(xerrno));
    } else {
        logger(4, "selectable " << selectable << " added for FD " << fd);
    }
}


void io::loop::remove_selectable(int fd)
{
    assert(this->epoll_fd != -1);

    if (epoll_ctl(this->epoll_fd, EPOLL_CTL_DEL, fd, NULL) < 0) {
        int xerrno = errno;
        logger(1, "epoll_ctl EPOLL_CTL_DEL failed for fd " << fd << io::error(xerrno));
    } else {
        logger(4, "selectable FD " << fd << " removed");
    }

    selectables[fd] = nullptr;
}

void io::loop::call_soon(std::function<void()> func)
{
    delayed_calls.push_back(io::delayed_call(std::chrono::seconds(0), func));
};

void io::loop::call_later(std::chrono::milliseconds dur, std::function<void()> func)
{
    delayed_calls.push_back(io::delayed_call(dur, func));
};

void io::loop::stop()
{
    running = false;
}

void io::loop::run()
{
    epoll_event *events;

    const int MAXEVENTS = 64;

    events = (epoll_event*)calloc(MAXEVENTS, sizeof(epoll_event));

    std::shared_ptr<io::selectable> selectable;
    while (running) {
        int n;

        const auto start = std::chrono::steady_clock::now();

        n = epoll_wait(this->epoll_fd, events, MAXEVENTS, 100);
        if (n == -1) {
            int xerrno = errno;
            logger(1, "error calling epoll_wait" << io::error(xerrno));
        } else {
            logger(5, n << " events to process");
        }

        epoll_event *ev;
        for (int i=0; i < n; i++) {
            ev = &events[i];

            try {
                selectable = selectables[ev->data.fd];

                assert(selectable);

                if (ev->events & EPOLLERR || ev->events & EPOLLHUP || (!ev->events & (EPOLLIN|EPOLLOUT))) {
                    selectable->do_close();
                } else if (ev->events & EPOLLIN) {
                    selectable->do_read();
                } else if (ev->events & EPOLLOUT) {
                    selectable->do_write();
                }
            }
            catch (std::exception const & e) {
                logger(1, "error on selectable FD " << ev->data.fd << " " << e.what());
            }
        }
        const std::chrono::milliseconds elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start);

        this->run_delayed_calls(elapsed);
    }
}

void io::loop::run_delayed_calls(const std::chrono::milliseconds &elapsed)
{
    delayed_calls.erase(
        std::remove_if(
            delayed_calls.begin(),
            delayed_calls.end(),
            [elapsed](io::delayed_call &delayed_call) -> bool {
                std::chrono::milliseconds delay;
                std::function<void()> call;
                std::tie(delay, call) = delayed_call;

                delay = delay - elapsed;
                if (delay.count() <= 0) {
                    call();
                    return true;
                } else {
                    std::get<0>(delayed_call) = delay;
                    return false;
                }
            }),
        delayed_calls.end()
    );
}
